import os
import asyncio
import time
import questionary
from utils.msg_printer import MsgPrinter
from utils.cli_questionary import WarningStyle
from modules.scrape_update_module import scrape_update
async def scrape_multi_update(scraped_data_root: str):
    """
    批量更新多个帖子
    """
    if not os.path.isdir(scraped_data_root):
        MsgPrinter.print_error(
            "输入的路径不存在",
            "ScrapeMultiUpdate",
        )
        return

    MsgPrinter.print_step_mark("开始批量更新多个帖子")
    update_start_time = time.time()

    # 查找所有包含 threads 文件夹的帖子目录
    thread_dirs = []
    for item in os.listdir(scraped_data_root):
        item_path = os.path.join(scraped_data_root, item)
        if os.path.isdir(item_path):
            # 检查是否包含 threads 文件夹
            threads_folder_path = os.path.join(item_path, "threads")
            if os.path.isdir(threads_folder_path):
                thread_dirs.append(item_path)

    if not thread_dirs:
        MsgPrinter.print_error(
            "未找到任何包含 threads 文件夹的帖子数据",
            "ScrapeMultiUpdate",
        )
        return

    MsgPrinter.print_tip(f"找到 {len(thread_dirs)} 个帖子数据文件夹")

    # 显示找到的帖子列表
    for i, thread_dir in enumerate(thread_dirs, 1):
        folder_name = os.path.basename(thread_dir)
        MsgPrinter.print_tip(f"{i}. {folder_name}")

    # 确认是否继续
    if not await questionary.confirm(
        f"是否开始更新这 {len(thread_dirs)} 个帖子？", 
        style=WarningStyle
    ).ask_async():
        return

    # 逐个更新帖子
    success_count = 0
    failed_count = 0
    failed_threads = []

    for i, thread_dir in enumerate(thread_dirs, 1):
        folder_name = os.path.basename(thread_dir)
        MsgPrinter.print_step_mark(f"开始更新第 {i}/{len(thread_dirs)} 个帖子")
        MsgPrinter.print_tip(f"帖子: {folder_name}")
        MsgPrinter.print_tip(f"路径: {thread_dir}")

        try:
            # 使用单帖更新功能，传入帖子文件夹路径
            await scrape_update(thread_dir)
            success_count += 1
            MsgPrinter.print_success(f"第 {i} 个帖子更新完成: {folder_name}")
        except Exception as e:
            failed_count += 1
            failed_threads.append(folder_name)
            MsgPrinter.print_error(
                f"更新帖子失败: {folder_name}",
                "ScrapeMultiUpdate",
                ["error", str(e)]
            )

            import traceback
            MsgPrinter.print_tip(f"详细错误: {traceback.format_exc()}")

        # 添加延迟避免请求过于频繁
        if i < len(thread_dirs):
            MsgPrinter.print_tip("等待 2 秒后继续下一个帖子...")
            await asyncio.sleep(2)

    # 输出汇总结果
    update_end_time = time.time()
    update_duration = update_end_time - update_start_time
    
    MsgPrinter.print_step_mark("批量更新完成")
    MsgPrinter.print_success(f"成功: {success_count} 个帖子")
    if failed_count > 0:
        MsgPrinter.print_error(f"失败: {failed_count} 个帖子")
        MsgPrinter.print_tip(f"失败的帖子: {', '.join(failed_threads)}")
    
    MsgPrinter.print_tip(f"总耗时 {int(update_duration // 60)} 分 {round(update_duration % 60, 2)} 秒")