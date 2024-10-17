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
            video_output_path = os.path.abspath(ydl.prepare_filename(info))
            return video_output_path, info['title']
    
    return video_output_path, None

# 提取音频并转换为mp3
def extract_audio(url, output_dir):
    audio_output_path = ''
    audio_opts = {
        'format': 'bestaudio/best',
        'outtmpl': os.path.join(output_dir, '%(title)s.%(ext)s'),
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
    
    with yt_dlp.YoutubeDL(audio_opts) as ydl:
        result = ydl.download([url])
        if result == 0:
            info = ydl.extract_info(url, download=False)
            audio_output_path = os.path.join(output_dir, f"{info['title']}.mp3")
            return audio_output_path
    
    return audio_output_path

# 音频转录函数，带分段处理并保存时间轴，并提供更加精确的进度更新
def transcribe_audio(audio_path, model, update_progress):
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


# 提取URL函数
def extract_urls(text):
    url_pattern = r'(https?://[^\s]+)'
    return re.findall(url_pattern, text)

class TranscriptionApp:
    def __init__(self, root):
        self.root = root
        self.root.title("视频转录工具")
        self.root.geometry("600x600")  # 增大窗口尺寸
        
        # URL输入框
        self.url_label = tk.Label(self.root, text="请输入视频URL或批量文本:")
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

        # 任务队列
        self.task_queue = queue.Queue()
        self.model = whisper.load_model("tiny")
        self.processing = False
        self.task_items = {}  # 用来跟踪任务索引

    # 开始转录函数，启动线程
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

    # 视频下载和转录的函数
    def transcribe_video(self, title, url):
        try:
            # 更新任务队列状态为“处理中”
            task_index = self.task_items[url]
            self.task_listbox.delete(task_index)
            self.task_listbox.insert(task_index, f"{task_index + 1}  [{title}]  [处理中 0%]")
            self.task_listbox.update()

            self.update_status(f"正在下载视频: {url}")

            # 创建单独的视频文件夹
            output_dir = os.path.abspath('output')
            clear_output_directory(output_dir, 50)
            
            video_path, title = download_video(url, output_dir)
            if not title:
                raise Exception("下载视频失败")

            video_folder = os.path.join(output_dir, title)
            if not os.path.exists(video_folder):
                os.makedirs(video_folder)
            
            # 提取音频并保存
            self.update_status(f"正在提取音频: {title}")
            audio_path = extract_audio(url, video_folder)
            
            # 开始转录
            self.update_status(f"正在转录: {title}")
            transcribed_text = transcribe_audio(audio_path, self.model, self.update_progress(task_index, title))

            # 保存转录文本
            transcript_file = os.path.join(video_folder, f"{title}.txt")
            with open(transcript_file, "w", encoding="utf-8") as f:
                f.write(transcribed_text)

            # 更新任务队列状态为“已完成”
            self.task_listbox.delete(task_index)
            self.task_listbox.insert(task_index, f"{task_index + 1}  [{title}]  [已完成]")
            self.task_listbox.update()

            self.update_status(f"完成转录: {title}")
        except Exception as e:
            self.update_status(f"处理失败: {str(e)}")
            messagebox.showerror("错误", str(e))

    # 更新状态显示
    def update_status(self, status):
        self.status_label.config(text="状态: " + status)

    # 更新进度显示
    def update_progress(self, task_index, title):
        def progress_callback(current_segment, total_segments):
            # 计算百分比
            percentage = int((current_segment / total_segments) * 100)

            # 在任务列表框中更新进度为百分比
            self.task_listbox.delete(task_index)
            self.task_listbox.insert(task_index, f"{task_index + 1}  [{title}]  [处理中 {percentage}%]")
            self.task_listbox.update()
            
            # 在状态栏中显示详细进度
            self.update_status(f"处理进度: {current_segment}/{total_segments} ({percentage}%)")
        
        return progress_callback



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
    # 设置默认编码为utf-8
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stdin.reconfigure(encoding='utf-8')

    root = tk.Tk()
    app = TranscriptionApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()