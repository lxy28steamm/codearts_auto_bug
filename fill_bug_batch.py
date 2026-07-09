"""
华为云自动化批量填Bug：
  IAM启动台 -> 华为云 -> 一汽大众捷达J01 -> 缺陷管理 -> 逐条新建Bug并填写

运行前请确保：
1. 已安装 playwright:  pip install playwright
2. 已安装浏览器:      playwright install chromium
3. Edge 已登录华为云，user_data_dir 路径正确
4. 将同目录下的 txt 文件路径配置到 BUG_TXT_PATH

使用方式：
  python fill_bug_batch.py
脚本会自动解析 txt 中的每条记录，逐条打开新建Bug界面并填写。
每条填写完成后会暂停，您可手动点击"保存"，然后按回车继续下一条。
"""
from playwright.sync_api import sync_playwright
import re
import os

# ================== 配置 ==================
# txt 文件路径（请根据实际情况修改）
BUG_TXT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output4.txt")


# Edge 用户数据目录（请根据实际情况修改）
USER_DATA_DIR = r"C:\Users\liuxinyi\AppData\Local\Microsoft\Edge\User\Data"

# 填写完每条后是否自动尝试保存（True=自动保存，False=暂停等待手动保存）
AUTO_SAVE = False


def parse_bugs(txt_path):
    """
    解析格式化txt，返回列表，每项为 dict: {title, description}
    """
    with open(txt_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 按空行分割记录（两个及以上换行视为分隔）
    raw_blocks = re.split(r"\n\s*\n", content.strip())

    bugs = []
    for block in raw_blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.splitlines()

        # 第一行是标题（以J01开头）
        title = lines[0].strip()
        if not title.startswith("J01"):
            # 如果不是J01开头，可能是残余空行，跳过
            continue

        # 其余行组合成描述
        # 按字段整理描述格式
        desc_lines = lines[1:]
        description = "\n".join(desc_lines).strip()

        bugs.append({
            "title": title,
            "description": description,
        })

    return bugs


def fill_bug_form(page, title, description):
    """在新建Bug界面填写标题和描述"""
    # ---- 填写标题 ----
    title_filled = False

    # 策略1: placeholder 包含 标题/主题/Bug标题
    if not title_filled:
        for kw in ["标题", "主题", "Bug标题", "请输入标题"]:
            loc = page.locator(f'input[placeholder*="{kw}"], textarea[placeholder*="{kw}"]')
            if loc.count() > 0:
                loc.first.fill(title)
                title_filled = True
                break

    # 策略2: aria-label / title
    if not title_filled:
        for kw in ["标题", "主题"]:
            loc = page.locator(f'input[aria-label*="{kw}"], textarea[aria-label*="{kw}"], input[title*="{kw}"]')
            if loc.count() > 0:
                loc.first.fill(title)
                title_filled = True
                break

    # 策略3: 第一个可见的单行/多行输入框
    if not title_filled:
        loc = page.locator('input[type="text"]:not([placeholder*="搜索"]):not([placeholder*="search"]), textarea')
        for i in range(min(loc.count(), 5)):
            elem = loc.nth(i)
            try:
                if elem.is_visible():
                    elem.fill(title)
                    title_filled = True
                    break
            except:
                continue

    if not title_filled:
        print("    ⚠ 标题输入框未定位到")
        return False

    # ---- 填写描述 ----
    desc_filled = False

    # 策略1: contenteditable div
    if not desc_filled:
        loc = page.locator('div[contenteditable="true"]')
        for i in range(min(loc.count(), 5)):
            elem = loc.nth(i)
            try:
                if elem.is_visible():
                    elem.click()
                    elem.fill(description)
                    desc_filled = True
                    break
            except:
                continue

    # 策略2: iframe 编辑器
    if not desc_filled:
        for idx, fr in enumerate(page.frames):
            try:
                body = fr.locator('body[contenteditable="true"], div[contenteditable="true"]')
                if body.count() > 0 and body.first.is_visible():
                    body.first.click()
                    body.first.fill(description)
                    desc_filled = True
                    break
            except:
                continue

    # 策略3: 可见的 textarea（高度较大）
    if not desc_filled:
        loc = page.locator('textarea')
        for i in range(min(loc.count(), 5)):
            elem = loc.nth(i)
            try:
                if elem.is_visible() and elem.evaluate('el => el.offsetHeight') > 80:
                    elem.fill(description)
                    desc_filled = True
                    break
            except:
                continue

    if not desc_filled:
        print("    ⚠ 描述编辑器未定位到")
        return False

    return True


def try_save_bug(page):
    """尝试点击保存按钮"""
    save_btn = None

    # 常见保存按钮文本
    for text in ["保存", "提交", "确定", "创建", "Save", "Create"]:
        loc = page.get_by_role("button", name=text, exact=True)
        if loc.count() == 0:
            loc = page.get_by_text(text, exact=True)
        if loc.count() > 0:
            save_btn = loc.first
            break

    if save_btn is None:
        # 兜底：找包含"保存"或"提交"的按钮
        for kw in ["保存", "提交"]:
            loc = page.locator("button").filter(has_text=kw)
            if loc.count() > 0:
                save_btn = loc.first
                break

    if save_btn:
        try:
            save_btn.click()
            print("    ✅ 已点击保存")
            return True
        except Exception as e:
            print(f"    ⚠ 点击保存失败: {e}")
            return False
    else:
        print("    ⚠ 未找到保存按钮")
        return False


def navigate_to_bug_list(page):
    """确保回到缺陷列表页，以便点击新建Bug"""
    # 如果当前不在缺陷列表，尝试点击左侧"缺陷管理"或顶部"缺陷"tab
    bug_tab = page.locator("a.devui-header-dynamic-link:has-text('缺陷管理')")
    if bug_tab.count() == 0:
        bug_tab = page.locator("a").filter(has_text="缺陷管理")
    if bug_tab.count() == 0:
        bug_tab = page.locator("a").filter(has_text="缺陷")
        # 找精确匹配"缺陷"的
        if bug_tab.count() > 0:
            for i in range(bug_tab.count()):
                if bug_tab.nth(i).text_content().strip() == "缺陷":
                    bug_tab = bug_tab.nth(i)
                    break

    if hasattr(bug_tab, 'count') and bug_tab.count() > 0:
        bug_tab.first.click()
        page.wait_for_load_state("networkidle", timeout=30000)
        page.wait_for_timeout(2000)
        return True
    return False


def main():
    # ---- 1. 解析txt ----
    if not os.path.exists(BUG_TXT_PATH):
        print(f"❌ 文件不存在: {BUG_TXT_PATH}")
        print("请修改脚本中的 BUG_TXT_PATH 为正确的txt文件路径")
        return

    bugs = parse_bugs(BUG_TXT_PATH)
    total = len(bugs)
    print(f"📄 共解析到 {total} 条Bug记录\n")

    if total == 0:
        print("❌ 未解析到任何记录，请检查txt文件格式")
        return

    # ---- 2. 启动浏览器 ----
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=USER_DATA_DIR,
            channel="msedge",
            headless=False,
            args=["--start-maximized"]
        )
        page = context.new_page()

        # ===== Step 1: IAM 启动台 =====
        page.goto("https://iam.navinfo.com/6528b3cd46fed68caf998b66/launchpad")
        page.wait_for_load_state("networkidle")
        print("Step 1: 启动台加载完成")

        # ===== Step 2: 点击华为云 =====
        huawei = page.get_by_text("华为云")
        if huawei.count() == 0:
            print("❌ 未找到'华为云'")
            input("按回车退出...")
            exit()

        with context.expect_page() as new_page_info:
            huawei.first.click()
            print("Step 2: 已点击'华为云'")

        page = new_page_info.value
        page.wait_for_load_state("networkidle", timeout=30000)
        page.wait_for_timeout(3000)
        print(f"  新页面 URL: {page.url}")

        # ===== Step 3: 进入项目 =====
        page.wait_for_selector("text=一汽大众捷达J01", timeout=15000)
        project = page.get_by_text("一汽大众捷达J01", exact=True)
        if project.count() == 0:
            project = page.get_by_text("一汽大众捷达J01")
        if project.count() == 0:
            project = page.locator("a,div,span").filter(has_text="一汽大众捷达J01")

        if project.count() == 0:
            print("❌ 未找到项目'一汽大众捷达J01'")
            page.screenshot(path="debug_no_project.png")
            input("按回车退出...")
            exit()

        project.first.click()
        print("Step 3: 已点击进入项目")
        page.wait_for_load_state("networkidle", timeout=30000)
        page.wait_for_timeout(5000)

        # ===== Step 4: 点击缺陷管理 =====
        bug_mgmt = page.locator("a.devui-header-dynamic-link:has-text('缺陷管理')")
        if bug_mgmt.count() == 0:
            bug_mgmt = page.locator("a").filter(has_text="缺陷管理")
        if bug_mgmt.count() == 0:
            bug_mgmt = page.locator("a").filter(has_text="缺陷")
            if bug_mgmt.count() > 0:
                for i in range(bug_mgmt.count()):
                    if bug_mgmt.nth(i).text_content().strip() == "缺陷":
                        bug_mgmt = bug_mgmt.nth(i)
                        break

        if hasattr(bug_mgmt, 'count') and bug_mgmt.count() == 0:
            print("❌ 未找到'缺陷管理'")
            page.screenshot(path="debug_no_bug_mgmt.png")
            input("按回车退出...")
            exit()
        if hasattr(bug_mgmt, 'first'):
            bug_mgmt = bug_mgmt.first

        bug_mgmt.click()
        print("Step 4: 已点击'缺陷管理'")
        page.wait_for_load_state("networkidle", timeout=30000)
        page.wait_for_timeout(3000)

        # ---- 3. 逐条处理 ----
        for idx, bug in enumerate(bugs, start=1):
            print(f"\n{'='*60}")
            print(f"【{idx}/{total}】正在处理: {bug['title'][:60]}...")
            print(f"{'='*60}")

            # ===== Step 5: 点击新建Bug =====
            new_bug = page.get_by_text("新建Bug", exact=True)
            if new_bug.count() == 0:
                new_bug = page.get_by_text("新建Bug")
            if new_bug.count() == 0:
                new_bug = page.get_by_text("新建bug")
            if new_bug.count() == 0:
                new_bug = page.get_by_role("button", name="新建Bug")
            if new_bug.count() == 0:
                new_bug = page.locator("button").filter(has_text="新建Bug")
            if new_bug.count() == 0:
                new_bug = page.get_by_text("新建")

            if new_bug.count() == 0:
                print("  ⚠ 未找到'新建Bug'，尝试回到缺陷列表...")
                if navigate_to_bug_list(page):
                    # 重新找新建Bug
                    new_bug = page.get_by_text("新建Bug")
                if new_bug.count() == 0:
                    print("  ❌ 仍然未找到'新建Bug'，跳过本条")
                    continue

            new_bug.first.click()
            print("  -> 已打开新建Bug界面")
            page.wait_for_timeout(3000)

            # ===== Step 6: 填写表单 =====
            ok = fill_bug_form(page, bug["title"], bug["description"])
            if ok:
                print("  ✅ 标题和描述填写完成")
                page.screenshot(path=f"bug_filled_{idx:03d}.png")
            else:
                print("  ⚠ 填写可能不完整")
                page.screenshot(path=f"bug_failed_{idx:03d}.png")

            # ===== Step 7: 保存或等待手动保存 =====
            if AUTO_SAVE:
                try_save_bug(page)
                page.wait_for_timeout(3000)
                # 尝试回到缺陷列表继续下一条
                navigate_to_bug_list(page)
            else:
                print(f"\n  💡 第 {idx}/{total} 条已填写完毕，请检查并手动点击【保存】")
                input(f"  保存完成后按回车继续下一条 ({idx}/{total}) ...")

        print(f"\n{'='*60}")
        print(f"🎉 全部 {total} 条记录处理完毕！")
        print(f"{'='*60}")
        input("\n按回车关闭浏览器...")
        context.close()


if __name__ == "__main__":
    main()
