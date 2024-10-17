import sys
import os
import shutil
import yt_dlp
import whisper
import warnings
import threading
import tkinter as tk
from tkinter import ttk, messagebox
import re
import queue
import requests
import json
import concurrent.futures


# 忽略所有警告
warnings.filterwarnings("ignore")

# 清理输出目录
def clear_output_directory(output_dir, max_files=10):
    if os.path.exists(output_dir):
        files = os.listdir(output_dir)
        if len(files) > max_files:
            for file in files:
                file_path = os.path.join(output_dir, file)
                if os.path.isfile(file_path):
                    os.remove(file_path)
                elif os.path.isdir(file_path):
                    shutil.rmtree(file_path)

# 文件名清理函数，去除非法字符
def sanitize_filename(filename):
    return re.sub(r'[<>:"/\\|?*]', '', filename).strip().replace(' ', '_')

# 下载视频函数
def download_video(url, output_dir):
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    video_output_path = ''
    
    def progress_hook(d):
        pass

    ydl_opts = {
        'format': 'bestvideo+bestaudio/best',
        'outtmpl': os.path.join(output_dir, '%(title)s.%(ext)s'),
        'merge_output_format': 'mp4',
        'quiet': True,
        'no_warnings': True,
        'progress_hooks': [progress_hook],
        'logger': None
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        result = ydl.download([url])
        if result == 0:
            info = ydl.extract_info(url, download=False)
            # 清理文件名中的非法字符
            clean_title = sanitize_filename(info['title'])
            video_output_path = os.path.abspath(os.path.join(output_dir, f"{clean_title}.mp4"))
            return video_output_path, clean_title
    
    return video_output_path, None

# 提取音频并转换为mp3
def extract_audio(url, output_dir):
    audio_output_path = ''
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # 先下载视频的元信息以获取标题
    with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
        info = ydl.extract_info(url, download=False)
        original_title = info['title']
        clean_title = sanitize_filename(original_title)  # 清理文件名中的非法字符

    ydl_opts = {
        'format': 'bestaudio/best',
        # 直接在下载时使用清理后的标题
        'outtmpl': os.path.join(output_dir, f"{clean_title}.%(ext)s"),
        'postprocessors': [
            {
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }
        ],
        'quiet': True,
        'no_warnings': True,
        'logger': None
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        result = ydl.download([url])
        if result == 0:
            audio_output_path = os.path.join(output_dir, f"{clean_title}.mp3")
            return audio_output_path
    
    return audio_output_path

# 音频转录函数，带分段处理并保存时间轴，并提供更加精确的进度更新
def transcribe_audio(self, audio_path, model, update_progress):
    # 获取音频文件的元信息，包括总时长
    result = model.transcribe(audio_path, verbose=False)
    
    # Whisper 返回的结果中包含了每个段的时间
    total_duration = result['segments'][-1]['end']  # 使用最后一段的结束时间作为音频的总时长

    transcription = []
    total_segments = len(result['segments'])

    # 计算进度并更新
    for i, segment in enumerate(result['segments']):
        start_time = segment['start']
        end_time = segment['end']
        text = segment['text']
        
        # 更新任务队列中的进度
        current_progress = (i + 1) / total_segments * 100  # 当前进度百分比
        update_progress(i + 1, total_segments)  # 传递当前段号和总段数

        # 格式化时间轴
        timestamp = f"[{start_time:.3f} --> {end_time:.3f}] {text.strip()}"
        transcription.append(timestamp)
    
    # 将所有转录内容拼接成一个字符串
    return "\n".join(transcription)
  
    
class TranscriptionApp:
    def __init__(self, root):
        self.root = root
        self.root.title("视频转录工具")
        self.root.geometry("600x900")  # 增大窗口尺寸

        # URL输入框
        self.url_label = tk.Label(self.root, text="批量输入B站或YTB的视频链接:")
        self.url_label.pack(pady=10)

        # 使用Text部件替代Entry部件，支持多行输入
        self.url_entry = tk.Text(self.root, width=50, height=10)
        self.url_entry.pack(pady=10)

        # 下载和转录按钮
        self.transcribe_button = ttk.Button(self.root, text="开始转录", command=self.start_transcription)
        self.transcribe_button.pack(pady=10)

        # 状态框
        self.status_label = tk.Label(self.root, text="状态: 等待操作")
        self.status_label.pack(pady=10)

        # 任务队列列表框
        self.task_listbox = tk.Listbox(self.root, width=70, height=10)
        self.task_listbox.pack(pady=10)

        # 监听列表项的双击事件，用于“预览”
        self.task_listbox.bind("<Double-Button-1>", self.show_preview)

        # 监听点击列表项的事件，用于渲染“总结”和“总结结果”按钮
        self.task_listbox.bind("<<ListboxSelect>>", self.render_buttons)

        # 任务队列
        self.task_queue = queue.Queue()
        self.model = whisper.load_model("tiny")
        self.processing = False
        self.task_items = {}  # 用来跟踪任务索引
        self.completed_tasks = set()  # 保存已经完成的任务的标题
        self.selected_task = None

        # 按钮
        self.summary_button = None
        self.analyzed_button = None
        
        # 添加“总结所有未总结视频”按钮
        self.summary_all_button = ttk.Button(self.root, text="总结所有未总结视频", command=self.start_summarize_all_thread)
        self.summary_all_button.pack(pady=10)

        # 在启动时加载已完成的任务
        self.load_completed_tasks()

    # 下载并转录视频的函数
    def transcribe_video(self, title, url):
        output_dir = os.path.abspath('output')
        
        # 更新任务状态为"下载中"
        task_index = self.task_items[url]
        self.task_listbox.delete(task_index)
        self.task_listbox.insert(task_index, f"{task_index + 1}  [{title}]  [下载中...]")
        self.task_listbox.update()

        # 下载视频并提取音频，返回清理后的音频路径
        audio_output_path = extract_audio(url, output_dir)

        if not audio_output_path:
            messagebox.showerror("下载错误", f"无法下载视频或提取音频: {title}")
            return

        # 更新任务状态为"转录中"
        self.task_listbox.delete(task_index)
        self.task_listbox.insert(task_index, f"{task_index + 1}  [{title}]  [转录中...]")
        self.task_listbox.update()

        # 开始转录音频，使用清理后的音频路径
        def update_progress(current_segment, total_segments):
            progress_percent = (current_segment / total_segments) * 100
            self.status_label.config(text=f"正在转录: {progress_percent:.2f}% 完成")
            self.status_label.update()

        transcription = self.transcribe_audio(audio_output_path, self.model, update_progress)

        # 保存转录文本
        transcript_file = os.path.join(output_dir, sanitize_filename(title), f"{sanitize_filename(title)}.txt")
        if not os.path.exists(os.path.dirname(transcript_file)):
            os.makedirs(os.path.dirname(transcript_file))

        with open(transcript_file, "w", encoding="utf-8") as f:
            f.write(transcription)

        # 更新任务状态为"已完成"
        self.task_listbox.delete(task_index)
        self.task_listbox.insert(task_index, f"{task_index + 1}  [{title}]  [已完成]")
        self.task_listbox.update()

        self.completed_tasks.add(title)


    # 音频转录函数，带分段处理并保存时间轴，并提供更加精确的进度更新
    def transcribe_audio(self, audio_path, model, update_progress):
        result = model.transcribe(audio_path, verbose=False)
        total_duration = result['segments'][-1]['end']  # 使用最后一段的结束时间作为音频的总时长

        transcription = []
        total_segments = len(result['segments'])

        # 计算进度并更新
        for i, segment in enumerate(result['segments']):
            start_time = segment['start']
            end_time = segment['end']
            text = segment['text']
            
            # 更新任务队列中的进度
            current_progress = (i + 1) / total_segments * 100  # 当前进度百分比
            update_progress(i + 1, total_segments)  # 传递当前段号和总段数

            # 格式化时间轴
            timestamp = f"[{start_time:.3f} --> {end_time:.3f}] {text.strip()}"
            transcription.append(timestamp)
        
        # 将所有转录内容拼接成一个字符串
        return "\n".join(transcription)

    def process_tasks(self):
        self.processing = True
        while not self.task_queue.empty():
            title, url = self.task_queue.get()
            self.transcribe_video(title, url)  # Now calling the correct method
        self.processing = False


    # 启动后台线程执行总结任务
    def start_summarize_all_thread(self):
        # 在开始时禁用按钮，防止重复点击
        self.summary_all_button.config(state="disabled")
        threading.Thread(target=self.summarize_all_unanalyzed_videos, daemon=True).start()
        
    # 执行总结所有未总结的视频（在后台线程中运行）
    def summarize_all_unanalyzed_videos(self):
        # 获取所有未总结的视频任务
        unanalyzed_tasks = []
        for i in range(self.task_listbox.size()):
            task_text = self.task_listbox.get(i)
            if "[已完成]" in task_text and "[已分析]" not in task_text:
                task_info = task_text.split("  ")
                if len(task_info) >= 2:
                    title = task_info[1].strip("[]")
                    unanalyzed_tasks.append(title)
        
        if not unanalyzed_tasks:
            messagebox.showinfo("总结完成", "没有未总结的视频")
            self.summary_all_button.config(state="normal")  # 无任务时重新启用按钮
            return

        # 使用线程池并发处理，最多同时发送8个请求
        max_threads = 8
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_threads) as executor:
            futures = [executor.submit(self.analyze_and_refresh_task, title) for title in unanalyzed_tasks]

            # 等待所有任务完成
            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    print(f"总结视频时发生错误: {e}")

        # 提示用户总结已完成
        messagebox.showinfo("总结完成", "所有未总结的视频已完成总结")

        # 在任务完成后重新启用按钮
        self.summary_all_button.config(state="normal")

    # 执行总结所有未总结的视频
    def summarize_all_unanalyzed_videos(self):
        # 获取所有未总结的视频任务
        unanalyzed_tasks = []
        for i in range(self.task_listbox.size()):
            task_text = self.task_listbox.get(i)
            if "[已完成]" in task_text and "[已分析]" not in task_text:
                task_info = task_text.split("  ")
                if len(task_info) >= 2:
                    title = task_info[1].strip("[]")
                    unanalyzed_tasks.append(title)
        
        if not unanalyzed_tasks:
            messagebox.showinfo("总结完成", "没有未总结的视频")
            return

        # 使用线程池并发处理，最多同时发送8个请求
        max_threads = 8
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_threads) as executor:
            futures = [executor.submit(self.analyze_and_refresh_task, title) for title in unanalyzed_tasks]

            # 等待所有任务完成
            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    print(f"总结视频时发生错误: {e}")

        messagebox.showinfo("总结完成", "所有未总结的视频已完成总结")

    def analyze_and_refresh_task(self, title):
        # 找到相应的转录文本文件
        output_dir = os.path.abspath('output')
        transcript_file = os.path.join(output_dir, title, f"{title}.txt")
        
        if not os.path.exists(transcript_file):
            print(f"找不到转录文件: {transcript_file}")
            return

        # 读取txt文件内容
        with open(transcript_file, "r", encoding="utf-8") as f:
            txt_file_content = f.read()

        # 准备API数据
        prompt = "以下是一段转写结果，有很多模型没听清的地方，你需要先尝试还原原文字幕。在还原完字幕后，为我总结视频内容："
        final_content = prompt + '\n' + txt_file_content

        data = {
            "model": "gpt-4o-mini",  # 修改为你需要的API模型
            "messages": [{"role": "user", "content": final_content}],
            "temperature": 0.0
        }

        api_key = 'sk-x4T9ByGTYlKzjaluQuVoQaA9MakHfra41Wkall2BcroNyxv2'  # 这里替换为你的API密钥
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}'
        }

        try:
            # 发送API请求
            response = requests.post('https://api.chatanywhere.tech/v1/chat/completions', headers=headers, data=json.dumps(data))

            # 检查HTTP状态码
            if response.status_code != 200:
                print(f"请求失败，状态码: {response.status_code}\n响应内容: {response.text}")
                return
            
            # 解析JSON响应
            response_json = response.json()

            # 检查是否有返回的结果
            if "choices" not in response_json or not response_json['choices']:
                print("没有找到有效的分析结果")
                return
            
            analysis_result = response_json['choices'][0]['message']['content']

            # 保存分析结果为 [视频标题]_已分析.txt
            analyzed_file = os.path.join(output_dir, title, f"{title}_已分析.txt")
            with open(analyzed_file, "w", encoding="utf-8") as f:
                f.write(analysis_result)

            print(f"分析结果已保存到: {analyzed_file}")

            # 分析完成后，刷新该任务的列表项状态
            self.refresh_task_item(title)
        
        except json.JSONDecodeError:
            print("无法解析API响应为有效的JSON数据")
        except Exception as e:
            print(f"API请求错误: {e}")
    
    # 加载output文件夹下的已完成任务
    def load_completed_tasks(self):
        output_dir = os.path.abspath('output')
        if not os.path.exists(output_dir):
            return

        for task_folder in os.listdir(output_dir):
            task_folder_path = os.path.join(output_dir, task_folder)
            transcript_file = os.path.join(task_folder_path, f"{task_folder}.txt")
            analyzed_file = os.path.join(task_folder_path, f"{task_folder}_已分析.txt")
            
            if os.path.exists(transcript_file):
                # 任务已经完成，渲染到任务列表中
                task_index = self.task_listbox.size() + 1
                display_text = f"{task_index}  [{task_folder}]  [已完成]"
                if os.path.exists(analyzed_file):
                    display_text += "  [已分析]"  # 如果存在已分析文件，标记它
                self.task_listbox.insert(tk.END, display_text)
                self.completed_tasks.add(task_folder)
                self.task_listbox.update()

    # 显示“预览”窗口，读取和展示文本文件内容
    def show_preview(self, event):
        # 获取当前选中的列表项
        selected_index = self.task_listbox.curselection()
        if not selected_index:
            return
        
        selected_index = selected_index[0]
        selected_task = self.task_listbox.get(selected_index)
        
        # 检查是否为“已完成”状态
        if "[已完成]" in selected_task:
            # 解析出标题
            task_info = selected_task.split("  ")
            if len(task_info) < 2:
                return
            title = task_info[1].strip("[]")
            
            # 找到相应的转录文本文件
            output_dir = os.path.abspath('output')
            transcript_file = os.path.join(output_dir, title, f"{title}.txt")
            
            if os.path.exists(transcript_file):
                # 创建一个新的窗口来显示“预览”
                preview_window = tk.Toplevel(self.root)
                preview_window.title(f"{title} 预览")
                preview_window.geometry("600x400")
                
                # 创建Text部件来显示文本文件内容
                preview_text = tk.Text(preview_window, wrap="word", width=70, height=25)
                preview_text.pack(expand=True, fill="both")
                
                # 读取并展示文件内容
                with open(transcript_file, "r", encoding="utf-8") as f:
                    content = f.read()
                    preview_text.insert(tk.END, content)
            else:
                messagebox.showwarning("文件不存在", f"找不到转录文件: {transcript_file}")

    # 渲染“总结”和“总结结果”按钮
    def render_buttons(self, event):
        # 移除之前的按钮
        if self.summary_button:
            self.summary_button.pack_forget()
        if self.analyzed_button:
            self.analyzed_button.pack_forget()

        # 获取当前选中的列表项
        selected_index = self.task_listbox.curselection()
        if not selected_index:
            return
        
        selected_index = selected_index[0]
        selected_task = self.task_listbox.get(selected_index)
        
        # 解析出标题
        task_info = selected_task.split("  ")
        if len(task_info) < 2:
            return
        title = task_info[1].strip("[]")

        output_dir = os.path.abspath('output')
        analyzed_file = os.path.join(output_dir, title, f"{title}_已分析.txt")
        
        # 如果该任务有"已完成"状态，渲染“总结”按钮
        if "[已完成]" in selected_task:
            self.selected_task = selected_task
            self.summary_button = ttk.Button(self.root, text="总结", command=self.analyze_text)
            self.summary_button.pack(pady=10)

        # 如果存在已分析文件，渲染“总结结果”按钮
        if os.path.exists(analyzed_file):
            self.analyzed_button = ttk.Button(self.root, text="总结结果", command=lambda: self.show_analyzed_result(analyzed_file))
            self.analyzed_button.pack(pady=10)

    # 查看"已分析"的总结结果
    def show_analyzed_result(self, analyzed_file):
        if not os.path.exists(analyzed_file):
            messagebox.showwarning("文件不存在", f"找不到分析文件: {analyzed_file}")
            return

        # 创建一个新的窗口来显示“总结结果”
        analyzed_window = tk.Toplevel(self.root)
        analyzed_window.title("总结结果")
        analyzed_window.geometry("600x400")

        # 创建Text部件来显示已分析文本文件内容
        analyzed_text = tk.Text(analyzed_window, wrap="word", width=70, height=25)
        analyzed_text.pack(expand=True, fill="both")

        # 读取并展示已分析文件内容
        with open(analyzed_file, "r", encoding="utf-8") as f:
            content = f.read()
            analyzed_text.insert(tk.END, content)

    # 将文本文件内容发送到API进行分析
    # 添加刷新列表项函数
    def refresh_task_item(self, title):
        # 遍历列表项并更新对应的任务状态
        for i in range(self.task_listbox.size()):
            task_text = self.task_listbox.get(i)
            if f"[{title}]" in task_text:
                new_text = f"{i + 1}  [{title}]  [已完成]  [已分析]"
                self.task_listbox.delete(i)  # 删除旧的列表项
                self.task_listbox.insert(i, new_text)  # 插入更新后的列表项
                self.task_listbox.update()  # 更新列表框显示
                break

    # 更新 analyze_text 函数
    def analyze_text(self):
        if not self.selected_task:
            return

        # 解析出标题
        task_info = self.selected_task.split("  ")
        if len(task_info) < 2:
            return
        title = task_info[1].strip("[]")

        # 找到相应的转录文本文件
        output_dir = os.path.abspath('output')
        transcript_file = os.path.join(output_dir, title, f"{title}.txt")
        
        if not os.path.exists(transcript_file):
            messagebox.showwarning("文件不存在", f"找不到转录文件: {transcript_file}")
            return

        # 读取txt文件内容
        with open(transcript_file, "r", encoding="utf-8") as f:
            txt_file_content = f.read()

        # 准备API数据
        prompt = "以下是一段转写结果，有很多模型没听清的地方，你需要先尝试还原原文字幕。在还原完字幕后，为我总结视频内容："
        final_content = prompt + '\n' + txt_file_content

        data = {
            "model": "gpt-4o-mini",  # 修改为你需要的API模型
            "messages": [{"role": "user", "content": final_content}],
            "temperature": 0.0
        }
        
        api_key = 'sk-x4T9ByGTYlKzjaluQuVoQaA9MakHfra41Wkall2BcroNyxv2'  # 这里替换为你的API密钥
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}'
        }

        try:
            # 发送API请求
            response = requests.post('https://api.chatanywhere.tech/v1/chat/completions', headers=headers, data=json.dumps(data))

            # 检查HTTP状态码
            if response.status_code != 200:
                messagebox.showerror("API错误", f"请求失败，状态码: {response.status_code}\n响应内容: {response.text}")
                return
            
            # 解析JSON响应
            response_json = response.json()

            # 检查是否有返回的结果
            if "choices" not in response_json or not response_json['choices']:
                messagebox.showerror("API错误", "没有找到有效的分析结果")
                return
            
            analysis_result = response_json['choices'][0]['message']['content']

            # 保存分析结果为 [视频标题]_已分析.txt
            analyzed_file = os.path.join(output_dir, title, f"{title}_已分析.txt")
            with open(analyzed_file, "w", encoding="utf-8") as f:
                f.write(analysis_result)

            messagebox.showinfo("分析完成", f"分析结果已保存到: {analyzed_file}")

            # 在分析完成后，刷新该任务的列表项状态
            self.refresh_task_item(title)
        
        except json.JSONDecodeError:
            messagebox.showerror("API错误", "无法解析API响应为有效的JSON数据")
        except Exception as e:
            messagebox.showerror("API错误", str(e))
    
    
    # 开始转录函数
    def start_transcription(self):
        urls_text = self.url_entry.get("1.0", tk.END).strip()  # 获取多行文本
        urls_with_titles = extract_urls_with_titles(urls_text)  # 提取标题和URL
        if not urls_with_titles:
            messagebox.showwarning("输入错误", "请输入有效的URL")
            return

        # 清除输入框内容
        self.url_entry.delete("1.0", tk.END)

        # 将任务添加到任务队列和列表框
        for title, url in urls_with_titles:
            # 如果任务已经存在于completed_tasks，跳过它
            if title in self.completed_tasks:
                continue

            task_index = self.task_listbox.size() + 1
            self.task_listbox.insert(tk.END, f"{task_index}  [{title}]  [等待中...]")
            self.task_listbox.update()

            # 将任务添加到队列
            self.task_queue.put((title, url))
            self.task_items[url] = task_index - 1  # 用于更新状态的索引

        # 如果没有任务在处理，启动处理线程
        if not self.processing:
            threading.Thread(target=self.process_tasks, daemon=True).start()

    # 处理任务队列
    def process_tasks(self):
        self.processing = True
        while not self.task_queue.empty():
            title, url = self.task_queue.get()
            self.transcribe_video(title, url)
        self.processing = False

# 提取URL和标题的函数
def extract_urls_with_titles(text):
    lines = text.splitlines()
    urls_with_titles = []
    
    for line in lines:
        # 假设标题和URL之间有空格，且URL以http/https开头
        match = re.search(r'(https?://[^\s]+)', line)
        if match:
            url = match.group(0)
            title = line[:match.start()].strip()  # 取出URL前面的部分作为标题
            urls_with_titles.append((title, url))
    
    return urls_with_titles

# 主函数
def main():
    root = tk.Tk()
    app = TranscriptionApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()