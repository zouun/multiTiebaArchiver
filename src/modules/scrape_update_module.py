import asyncio
import os
import time

import orjson
import questionary

import config.scraper_config as scraper_config
from api.aiotieba_client import get_posts
from config.path_config import ScrapeDataPathBuilder
from container.container import Container
from pojo.scrape_info import ScrapeInfoDict, ScrapeRecordDict
from scrape_config import ScrapeConfig, ScrapeConfigKeys
from services.post_service import PostService
from services.thread_service import ThreadService
from services.user_service import UserService
from utils.cli_questionary import WarningStyle
from utils.common import json_dumps
from utils.logger import generate_scrape_logger_msg
from utils.msg_printer import MsgPrinter


async def scrape_update(path: str):
    if not os.path.isdir(path):
        MsgPrinter.print_error(
            "输入的路径不存在",
            "ScrapeUpdate",
        )
        return

    MsgPrinter.print_step_mark("开始读取本地数据")
    update_start_time = time.time()
    Container.set_scrape_timestamp(int(update_start_time))

    scrape_data_path_builder = ScrapeDataPathBuilder.get_instance_scrape_update(path)
    Container.set_scrape_data_path_builder(scrape_data_path_builder)

    # ANCHOR scrape_info.json
    scrape_info_path = scrape_data_path_builder.get_scrape_info_path()
    if not os.path.isfile(scrape_info_path):
        MsgPrinter.print_error(
            "未找到文件'scrape_info.json'",
            "ScrapeUpdate",
        )
        return

    with open(scrape_info_path, "r", encoding="utf-8") as file:
        scrape_info: ScrapeInfoDict = orjson.loads(file.read())

    new_scrape_record: ScrapeRecordDict = {
        "scrape_time": Container.get_scrape_timestamp(),
        "scrape_config": ScrapeConfig.to_dict(),
    }
    if "scrape_records" in scrape_info and len(scrape_info["scrape_records"]) > 0:
        scrape_records = scrape_info["scrape_records"]
        if not (
                await confirm_config(scrape_records[-1]["scrape_config"], new_scrape_record["scrape_config"])
        ):
            return

        scrape_info["scrape_records"].append(new_scrape_record)
    else:
        scrape_info["scrape_records"] = [new_scrape_record]

    scrape_info["update_time"] = Container.get_scrape_timestamp()
    scrape_info["scraper_version"] = scraper_config.SCRAPER_VERSION
    with open(scrape_info_path, "w", encoding="utf-8") as file:
        file.write(json_dumps(scrape_info))

    # ANCHOR main_thread
    main_thread_id = scrape_info["main_thread"]
    await update_thread(main_thread_id)

    with open(scrape_data_path_builder.get_thread_info_path(main_thread_id), "r", encoding="utf-8") as file:
        main_thread_info_dict = orjson.loads(file.read())

    # ANCHOR share_origin
    share_origin_id = main_thread_info_dict["share_origin"]
    if share_origin_id != 0:
        MsgPrinter.print_step_mark("开始处理 share_origin")
        await update_thread(share_origin_id, is_share_origin=True)

    update_end_time = time.time()
    update_duration = update_end_time - update_start_time
    MsgPrinter.print_step_mark("任务完成.")
    MsgPrinter.print_tip(f"耗时 {int(update_duration // 60)} 分 {round(update_duration % 60, 2)} 秒.")


async def update_thread(tid: int, *, is_share_origin=False):
    if tid <= 0:
        return

    Container.set_tid(tid)
    content_db = Container.get_content_db()
    scrape_logger = Container.get_scrape_logger()

    def final_treatment():
        content_db.close()

    # 在这里判断的原因是为了更新 share_origin 数据库的数据结构。
    if is_share_origin and (not ScrapeConfig.UPDATE_SHARE_ORIGIN):
        final_treatment()

    scrape_logger.info(generate_scrape_logger_msg("开始更新帖子", "StepMark", ["tid", tid]))

    pre_fetch_posts = await get_posts(tid)
    if pre_fetch_posts is None:
        final_treatment()
        MsgPrinter.print_error(f"帖子可能已删除", "ScrapeUpdate", ["tid", tid])
        scrape_logger.error(generate_scrape_logger_msg("帖子可能已删除", "ScrapeUpdate", ["tid", tid]))
        return

    post_service = PostService()
    user_service = UserService()
    thread_service = ThreadService()

    MsgPrinter.print_step_mark("开始更新 forum, thread 元信息", ["tid", tid])
    await asyncio.gather(
        thread_service.save_forum_info(pre_fetch_posts.forum.fid),
        thread_service.save_thread_info(pre_fetch_posts.thread),
    )

    MsgPrinter.print_step_mark("开始更新 posts", ["tid", tid])
    await post_service.scrape_post(pre_fetch_posts.page.total_page, is_update=True)

    MsgPrinter.print_step_mark("开始集中完善用户信息", ["tid", tid])
    scrape_logger.info(generate_scrape_logger_msg("正在集中完善用户数据", "StepMark", ["tid", tid]))
    await user_service.complete_user_info()

    final_treatment()
    MsgPrinter.print_step_mark("帖子更新完成", ["tid", tid])
    scrape_logger.info(generate_scrape_logger_msg("帖子更新完成", "StepMark", ["tid", tid]))


# 确认配置
async def confirm_config(old_config: dict, new_config: dict) -> bool:

    confirm_item = [
        (
            ScrapeConfigKeys.POST_FILTER_TYPE,
            "帖子过滤",
        ),
        (
            ScrapeConfigKeys.DOWNLOAD_USER_AVATAR_MODE,
            "用户头像下载模式",
        ),
    ]

    is_diff = False
    diff_msg = ""

    for item in confirm_item:
        if old_config[item[0]] != new_config[item[0]]:
            is_diff = True

            diff_msg += f"{item[1]}: {old_config[item[0]]} -> {new_config[item[0]]}\n"

    if is_diff:
        print(diff_msg)
        return await questionary.confirm(
            "本次爬取的配置与上一次爬取的配置存在差异，是否继续？", style=WarningStyle
        ).ask_async()
    else:
        return True

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
            # 记录详细错误信息
            import traceback
            MsgPrinter.print_tip(f"详细错误: {traceback.format_exc()}")

        # 可选：添加延迟避免请求过于频繁
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