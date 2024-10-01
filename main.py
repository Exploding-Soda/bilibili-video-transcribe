import sys
import os
import shutil
import yt_dlp
import whisper
import warnings
import threading
import tkinter as tk
from tkinter import ttk, messagebox

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
def download_video(url):
    output_dir = os.path.abspath('output')
    clear_output_directory(output_dir, 10)
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
            video_output_path = os.path.abspath(ydl.prepare_filename(ydl.extract_info(url, download=False)))
    
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
        'progress_hooks': [progress_hook],
        'logger': None
    }
    
    with yt_dlp.YoutubeDL(audio_opts) as ydl:
        result = ydl.download([url])
        if result == 0:
            audio_output_path = os.path.abspath(ydl.prepare_filename(ydl.extract_info(url, download=False)))
            audio_output_path = audio_output_path.rsplit('.', 1)[0] + '.mp3'
    
    return video_output_path, audio_output_path

# 音频转录函数
def transcribe_audio(audio_path):
    model = whisper.load_model("tiny")
    result = model.transcribe(audio_path)
    return result["text"]

# 主应用类
class TranscriptionApp:
    def __init__(self, root):
        self.root = root
        self.root.title("视频转录工具")
        self.root.geometry("500x400")
        
        # URL输入框
        self.url_label = tk.Label(self.root, text="请输入视频URL:")
        self.url_label.pack(pady=10)
        
        self.url_entry = tk.Entry(self.root, width=50)
        self.url_entry.pack(pady=10)
        
        # 下载和转录按钮
        self.transcribe_button = ttk.Button(self.root, text="开始转录", command=self.start_transcription)
        self.transcribe_button.pack(pady=10)

        # 状态框
        self.status_label = tk.Label(self.root, text="状态: 等待操作")
        self.status_label.pack(pady=10)

        # 转录结果显示框
        self.result_label = tk.Label(self.root, text="转录结果:")
        self.result_label.pack(pady=10)
        
        self.result_text = tk.Text(self.root, height=10, width=60)
        self.result_text.pack(pady=10)

    # 开始转录函数，启动线程
    def start_transcription(self):
        url = self.url_entry.get()
        if not url:
            messagebox.showwarning("输入错误", "请输入有效的URL")
            return
        
        # 使用线程来避免阻塞UI
        threading.Thread(target=self.transcribe_video, args=(url,), daemon=True).start()

    # 视频下载和转录的函数
    def transcribe_video(self, url):
        try:
            # 更新状态
            self.update_status("正在下载视频...")
            video_path, audio_path = download_video(url)

            # 视频下载完成后更新状态
            self.update_status("视频下载完成，正在转录音频...")

            # 转录音频
            transcribed_text = transcribe_audio(audio_path)

            # 更新状态并显示转录文本
            self.update_status("转录完成")
            self.display_transcription(transcribed_text)
        except Exception as e:
            self.update_status("获取视频或转录失败")
            messagebox.showerror("错误", str(e))

    # 更新状态显示
    def update_status(self, status):
        self.status_label.config(text="状态: " + status)
    
    # 显示转录结果
    def display_transcription(self, text):
        self.result_text.delete(1.0, tk.END)
        self.result_text.insert(tk.END, text)

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
