#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# (c) Shrimadhav U K

import os
import time
import json
import asyncio
import queue
import subprocess
from datetime import datetime
from PIL import Image
from hachoir.metadata import extractMetadata
from hachoir.parser import createParser
from pyrogram import Client, enums
from pyrogram.types import CallbackQuery
from pyrogram.errors import FloodWait
from .display_progress import progress_for_pyrogram
from .help_Nekmo_ffmpeg import generate_screen_shots
from .helper import run_cmd, ffmpeg_supported_video_mimetypes
from .. import client
from yt_dlp import YoutubeDL

async def youtube_dl_call_back(bot: Client, update: CallbackQuery):
    cb_data = update.data
    tg_send_type, youtube_dl_format, youtube_dl_ext = cb_data.split("|")
    thumb_image_path = client.config.DOWNLOAD_LOCATION + "/" + str(update.from_user.id) + ".jpg"
    if client.custom_thumbnail.get(update.from_user.id):
        thumb_image_path = client.custom_thumbnail.get(update.from_user.id)
    save_ytdl_json_path = client.config.DOWNLOAD_LOCATION + "/" + str(update.from_user.id) + ".json"
    try:
        with open(save_ytdl_json_path, "r", encoding="utf8") as f:
            response_json = json.load(f)
    except FileNotFoundError:
        await bot.edit_message_text(
            text=client.translation.NO_VOID_FORMAT_FOUND.format("JSON Not Found"),
            chat_id=update.message.chat.id,
            message_id=update.message.id
        )
        return False
    youtube_dl_url = update.message.reply_to_message.text
    custom_file_name = client.custom_caption.get(update.from_user.id) if client.custom_caption.get(update.from_user.id) else client.translation.CUSTOM_CAPTION_UL_FILE.format(bot.me.mention)
    description = custom_file_name
    start = datetime.now()
    progress_message = await bot.send_message(
        chat_id=update.message.chat.id,
        text=client.translation.DOWNLOAD_START,
        reply_to_message_id=update.message.reply_to_message.id
    )

    # Thread-safe queue for progress updates
    progress_queue = queue.Queue()

    # Async task to consume progress queue
    async def process_progress_queue():
        while True:
            try:
                downloaded, total = progress_queue.get_nowait()
                await progress_for_pyrogram(
                    downloaded,
                    total,
                    "Downloading...",
                    progress_message,
                    start.timestamp()
                )
                progress_queue.task_done()
            except queue.Empty:
                await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                break

    # Start queue processing task
    queue_task = asyncio.create_task(process_progress_queue())

    # yt-dlp progress hook
    def progress_hook(d):
        if d['status'] == 'downloading':
            downloaded = d.get('downloaded_bytes', 0)
            total = d.get('total_bytes', d.get('total_bytes_estimate', 0))
            if total > 0:
                progress_queue.put((downloaded, total))
            else:
                client.logger.debug(f"No total_bytes for {youtube_dl_url}: {d}")
        elif d['status'] == 'error':
            client.logger.debug(f"yt-dlp error: {d.get('error', 'Unknown error')}")

    # Simplified ydl_opts to match working bot
    is_audio_only = "audio" in tg_send_type
    output_path = f"{client.config.DOWNLOAD_LOCATION}/{update.from_user.id}"
    ydl_opts = {
        'format': f"{youtube_dl_format}+bestaudio/best" if not is_audio_only else youtube_dl_format,
        'outtmpl': output_path,
        'noplaylist': True,
        'progress_hooks': [progress_hook],
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'merge_output_format': 'mp4' if not is_audio_only else None,
        'quiet': False,
        'verbose': True,
        'no_warnings': False,
    }
    if is_audio_only:
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
    if client.config.HTTP_PROXY:
        ydl_opts['proxy'] = client.config.HTTP_PROXY

    # Run yt-dlp as a subprocess to avoid threading issues
    download_directory = f"{output_path}.{youtube_dl_ext}"
    if is_audio_only:
        download_directory = f"{output_path}.mp3"
    try:
        cmd = [
            'yt-dlp',
            '-f', ydl_opts['format'],
            '-o', output_path,
            '--user-agent', ydl_opts['user_agent'],
            '--no-playlist',
            '--verbose'
        ]
        if is_audio_only:
            cmd.extend(['--extract-audio', '--audio-format', 'mp3', '--audio-quality', '192'])
        if client.config.HTTP_PROXY:
            cmd.extend(['--proxy', client.config.HTTP_PROXY])
        cmd.append(youtube_dl_url)
        client.logger.debug(f"Running yt-dlp command: {' '.join(cmd)}")
        process = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120  # 2-minute timeout
        )
        if process.returncode != 0:
            error_msg = process.stderr or "Unknown yt-dlp error"
            client.logger.debug(f"yt-dlp subprocess failed for {youtube_dl_url}: {error_msg}")
            raise subprocess.CalledProcessError(process.returncode, cmd, process.stdout, process.stderr)
        # Rename audio file if needed
        if is_audio_only and not os.path.exists(download_directory) and os.path.exists(output_path):
            os.rename(output_path, download_directory)
    except subprocess.TimeoutExpired:
        queue_task.cancel()
        client.logger.debug(f"Download timed out for {youtube_dl_url}")
        await bot.edit_message_text(
            text=client.translation.NO_VOID_FORMAT_FOUND.format("Download timed out"),
            chat_id=update.message.chat.id,
            message_id=progress_message.id
        )
        return False
    except subprocess.CalledProcessError as e:
        queue_task.cancel()
        client.logger.debug(f"yt-dlp subprocess failed for {youtube_dl_url}: {e.stderr}")
        await bot.edit_message_text(
            text=client.translation.NO_VOID_FORMAT_FOUND.format(e.stderr or "yt-dlp failed"),
            chat_id=update.message.chat.id,
            message_id=progress_message.id
        )
        return False
    except Exception as e:
        queue_task.cancel()
        client.logger.debug(f"Download failed for {youtube_dl_url}: {str(e)}")
        await bot.edit_message_text(
            text=client.translation.NO_VOID_FORMAT_FOUND.format(str(e)),
            chat_id=update.message.chat.id,
            message_idVisualViewportWidth: 100%
    )
    return False
    finally:
        queue_task.cancel()

    if os.path.exists(download_directory):
        end_one = datetime.now()
        await bot.edit_message_text(
            text=client.translation.UPLOAD_START,
            chat_id=update.message.chat.id,
            message_id=progress_message.id
        )
        file_size = client.config.TG_MAX_FILE_SIZE + 1
        try:
            file_size = os.stat(download_directory).st_size
        except FileNotFoundError as exc:
            download_directory = download_directory.replace(".mkv", ".mp4")
            download_directory = download_directory.replace(".webm", ".mp4")
            file_size = os.stat(download_directory).st_size
        if file_size > client.config.TG_MAX_FILE_SIZE:
            await bot.edit_message_text(
                chat_id=update.message.chat.id,
                text=client.translation.RCHD_TG_API_LIMIT.format(humanbytes(file_size)),
                message_id=progress_message.id
            )
        else:
            is_screenshotable = False
            duration = 0
            width = 0
            height = 0
            if tg_send_type != "audio" and client.guess_mime_type(download_directory) in ffmpeg_supported_video_mimetypes:
                metadata = extractMetadata(createParser(download_directory))
                if metadata is not None:
                    if metadata.has("duration"):
                        duration = metadata.get('duration').seconds
                is_screenshotable = True
            if not os.path.exists(thumb_image_path) and client.guess_mime_type(download_directory) in ffmpeg_supported_video_mimetypes:
                await run_cmd('ffmpeg -ss {} -i "{}" -vframes 1 "{}"'.format(0, download_directory, thumb_image_path))
            if os.path.exists(thumb_image_path):
                metadata = extractMetadata(createParser(thumb_image_path))
                if metadata is not None:
                    if metadata.has("width"):
                        width = metadata.get("width")
                    if metadata.has("height"):
                        height = metadata.get("height")
                    Image.open(thumb_image_path).convert("RGB").save(thumb_image_path)
                    img = Image.open(thumb_image_path)
                    if tg_send_type == "file":
                        img.resize((320, height))
                    else:
                        img.resize((90, height))
                    img.save(thumb_image_path, "JPEG")
            else:
                thumb_image_path = None
            performer = None
            title = None
            if "artist" in response_json:
                performer = response_json["artist"]
            if "title" in response_json:
                title = response_json["title"]
            start_time = time.time()
            media = None
            try:
                if tg_send_type == "audio":
                    media = await bot.send_audio(
                        chat_id=update.message.chat.id,
                        audio=download_directory,
                        caption=description,
                        duration=duration,
                        performer=performer,
                        title=title,
                        thumb=thumb_image_path,
                        reply_to_message_id=update.message.reply_to_message.id,
                        progress=progress_for_pyrogram,
                        progress_args=(
                            client.translation.UPLOAD_START,
                            progress_message,
                            start_time
                        )
                    )
                elif tg_send_type == "file":
                    media = await bot.send_document(
                        chat_id=update.message.chat.id,
                        document=download_directory,
                        thumb=thumb_image_path,
                        caption=description,
                        reply_to_message_id=update.message.reply_to_message.id,
                        progress=progress_for_pyrogram,
                        progress_args=(
                            client.translation.UPLOAD_START,
                            progress_message,
                            start_time
                        )
                    )
                elif tg_send_type == "video":
                    media = await bot.send_video(
                        chat_id=update.message.chat.id,
                        video=download_directory,
                        caption=description,
                        duration=duration,
                        width=width,
                        height=height,
                        supports_streaming=True,
                        thumb=thumb_image_path,
                        reply_to_message_id=update.message.reply_to_message.id,
                        progress=progress_for_pyrogram,
                        progress_args=(
                            client.translation.UPLOAD_START,
                            progress_message,
                            start_time
                        )
                    )
                elif tg_send_type == "vm":
                    media = await bot.send_video_note(
                        chat_id=update.message.chat.id,
                        video_note=download_directory,
                        duration=duration,
                        length=width,
                        thumb=thumb_image_path,
                        reply_to_message_id=update.message.reply_to_message.id,
                        progress=progress_for_pyrogram,
                        progress_args=(
                            client.translation.UPLOAD_START,
                            progress_message,
                            start_time
                        )
                    )
            except Exception as e:
                await bot.edit_message_text(
                    text=client.translation.NO_VOID_FORMAT_FOUND.format(str(e)),
                    chat_id=update.message.chat.id,
                    message_id=progress_message.id
                )
                return False
            end_two = datetime.now()
            if is_screenshotable and client.config.SHOULD_GENERATE_SCREEN_SHOTS:
                await bot.send_chat_action(update.message.chat.id, enums.ChatAction.UPLOAD_PHOTO)
                images = await generate_screen_shots(
                    download_directory,
                    client.config.DOWNLOAD_LOCATION,
                    client.config.SHOULD_WATER_MARK,
                    client.config.DEF_WATER_MARK_FILE,
                    360,
                    client.config.NO_OF_PHOTOS
                )
                i = 0
                caption = client.translation.SS_TAKEN_FROM.format(client.config.SS_RANGE)
                for image in images:
                    if os.path.exists(str(image)):
                        if i == 0:
                            await bot.send_photo(
                                update.message.chat.id,
                                photo=str(image),
                                caption=caption,
                                reply_to_message_id=media.id
                            )
                            i += 1
                        else:
                            await bot.send_photo(
                                update.message.chat.id,
                                photo=str(image),
                                reply_to_message_id=media.id
                            )
                        os.remove(str(image))
            if client.config.DUMP_ID:
                await media.copy(client.config.DUMP_ID, caption=f'User Name: {update.from_user.first_name}\nUser ID: {update.from_user.id}\nLink: {youtube_dl_url}')
            os.remove(download_directory)
            if not client.custom_thumbnail.get(update.from_user.id):
                if thumb_image_path and os.path.isfile(thumb_image_path):
                    os.remove(thumb_image_path)
            time_taken_for_download = (end_one - start).seconds
            time_taken_for_upload = (end_two - end_one).seconds
            for attempt in range(3):
                try:
                    await bot.edit_message_text(
                        text=client.translation.AFTER_SUCCESSFUL_UPLOAD_MSG_WITH_TS.format(
                            time_taken_for_download, time_taken_for_upload),
                        chat_id=update.message.chat.id,
                        message_id=progress_message.id,
                        disable_web_page_preview=True
                    )
                    break
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                except Exception as e:
                    client.logger.debug(f"Failed to edit success message: {e}")
                    await asyncio.sleep(1)
    else:
        await bot.edit_message_text(
            text=client.translation.NO_VOID_FORMAT_FOUND.format("Incorrect Link"),
            chat_id=update.message.chat.id,
            message_id=progress_message.id,
            disable_web_page_preview=True
        )
