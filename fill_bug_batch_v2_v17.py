"""
华为云自动化批量填Bug（增强版V12 - 修复定位不准和面板关闭问题）：
  IAM启动台 -> 华为云 -> 一汽大众捷达J01 -> 缺陷管理 -> 逐条新建Bug并填写

本版修复：
  1. V11中 close_all_dropdowns 不够可靠，老点归属功能的下拉列表
  2. get_trigger_by_label 过度依赖 ancestor div + class 匹配，定位不准
  3. 改用 JS 强制隐藏所有 overlay 面板，确保100%关闭
  4. get_trigger_by_label 改为更直接的 following::input[1] 定位，不限制 class
  5. 移除 DEFAULT_VALUES 中的"归属项目"（默认已填好）

运行前请确保：
1. 已安装 playwright:  pip install playwright
2. 已安装浏览器:      playwright install chromium
3. Edge 已登录华为云，user_data_dir 路径正确
4. 将同目录下的 txt 文件路径配置到 BUG_TXT_PATH
5. 按需修改下方 DEFAULT_VALUES 中的默认值

使用方式：
  python fill_bug_batch_v2_v12.py
"""
from playwright.sync_api import sync_playwright
import re
import os

# ================== 配置 ==================
BUG_TXT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "zyb.txt")
USER_DATA_DIR = r"C:\Users\liuxinyi\AppData\Local\Microsoft\Edge\User\Data"
AUTO_SAVE = False          # 填写完每条后是否自动尝试保存
AUTO_DROPDOWNS = True      # 是否自动填写下拉列表

# ---- 下拉框速度参数（页面不稳定时可适当调大）----
FAST_DROPDOWN_MODE = True
WAIT_CLOSE_MS = 40         # 关闭旧面板后的等待
WAIT_PANEL_MS = 400        # 等待新下拉面板展开的最长时间
WAIT_SEARCH_MS = 200       # 输入搜索内容后等待筛选结果
WAIT_CLICK_MS = 80         # 点击选项后的等待
WAIT_CLEAR_MS = 120        # 点击"提出人"小叉后的等待
WAIT_RETRY_MS = 250        # 首次展开失败后的重试等待

# 选项数量少时优先用键盘导航的字段（比搜索+点击更快）。
# 注意：只放 devui-select-input 类型的字段；devui-select-placeholder 类型（如"严重程度"）
# 键盘导航焦点不对，会填不上。
KEYBOARD_FIRST_LABELS = {"发现阶段"}

# ---- 下拉框默认值（按需修改） ----
# 注意："归属项目"默认已填好，已从本配置中移除
DEFAULT_VALUES = {
    "归属功能": "APA",
    "提出人": "张玉宝94270",
    "当前责任人": "闫小伟93436",
    "跟踪人": "杨学敏",
    "研发模块": "泊车-应用算法",
    "一级标签-现象": "系统故障",
    # "ALM状态": "",        # 留空表示不填
    "发现阶段": "系统测试",
    "严重程度": "一般",
    "问题来源": "实车",
    "bug复现率": "仅此一次",
    # "抄送人": "",         # 留空表示不填
}


# ================== 解析 txt ==================
def parse_bugs(txt_path):
    """解析格式化txt，返回列表，每项为 dict: {title, description}"""
    with open(txt_path, "r", encoding="utf-8") as f:
        content = f.read()

    raw_blocks = re.split(r"\n\s*\n", content.strip())
    bugs = []
    for block in raw_blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.splitlines()
        title = lines[0].strip()
        if not title.startswith("J01"):
            continue
        description = "\n".join(lines[1:]).strip()
        bugs.append({"title": title, "description": description})
    return bugs


# ================== 辅助函数 ==================
def close_all_dropdowns(page):
    """
    可靠地关闭所有打开的下拉面板。
    V12 核心改进：使用 JS 强制隐藏所有 overlay 面板，确保100%关闭。
    V17 优化：快速模式下合并步骤，减少总等待时间。
    """
    # 1. 强制 blur + JS 强制隐藏所有已知类型的下拉面板（一步完成）
    try:
        page.evaluate("""
            document.querySelectorAll('input, textarea, [contenteditable]').forEach(el => el.blur());
            var selectors = [
                '.devui-dropdown-menu', '.devui-select-dropdown', '.devui-dropdown-panel',
                '.cdk-overlay-pane', '.cdk-overlay-backdrop',
                '.d-select-dropdown', '.ng-dropdown-panel',
                '.select-dropdown', '.dropdown-menu', '.select-menu',
                '[class*="dropdown-panel"]', '[class*="select-panel"]'
            ];
            selectors.forEach(function(sel) {
                document.querySelectorAll(sel).forEach(function(el) {
                    el.style.display = 'none';
                    el.style.visibility = 'hidden';
                    el.style.opacity = '0';
                });
            });
        """)
    except:
        pass
    page.wait_for_timeout(WAIT_CLOSE_MS if FAST_DROPDOWN_MODE else 400)

    # 2. 按 ESC + 点击页面左上角空白处（一步完成）
    page.keyboard.press("Escape")
    try:
        page.mouse.click(10, 10)
    except:
        pass
    page.wait_for_timeout(WAIT_CLOSE_MS if FAST_DROPDOWN_MODE else 300)


def wait_until_panel_visible(page, timeout_ms=None, interval_ms=80):
    """短轮询等待下拉面板出现，比固定等待更快。"""
    if timeout_ms is None:
        timeout_ms = WAIT_PANEL_MS if FAST_DROPDOWN_MODE else 1500
    elapsed = 0
    while elapsed < timeout_ms:
        if is_panel_visible(page):
            return True
        page.wait_for_timeout(interval_ms)
        elapsed += interval_ms
    return is_panel_visible(page)


def fast_fill_input(locator, value):
    """优先用 fill 快速输入；失败时回退到 type。"""
    try:
        locator.fill(value)
        return True
    except:
        try:
            locator.evaluate("el => el.select()")
            locator.type(value)
            return True
        except:
            return False


def js_click(page, locator_or_element):
    """使用 JavaScript 点击，绕过元素遮挡。"""
    try:
        if hasattr(locator_or_element, 'evaluate'):
            locator_or_element.evaluate("el => el.click()")
        else:
            locator_or_element.first.evaluate("el => el.click()")
        return True
    except Exception as e:
        print(f"    ⚠ JS点击失败: {e}")
        return False


def get_trigger_by_label(page, label):
    """
    通过 label 文本精确定位下拉框触发器。
    V12 核心改进：不再依赖 ancestor div + class，改用更直接的 XPath。
    优先处理截图中“提出人/严重程度”这种没有 input、只有 devui-select-placeholder 的选择框；
    其它字段再使用 following::input[1]，找不到再向上回溯找同组 input。
    返回 trigger locator 或 None。
    """
    # 策略A0: “提出人/严重程度”控件截图里选中的是 div.devui-select-placeholder，而不是 input。
    # 如果直接用 following::input[1]，会跳过当前字段的占位 div，误点到后面的其它输入框/选择框。
    placeholder_select_labels = {"提出人", "严重程度"}
    if label in placeholder_select_labels:
        for xpath in [
            f"xpath=//label[normalize-space()='{label}']/following::div[contains(@class,'devui-select-placeholder')][1]",
            f"xpath=//span[normalize-space()='{label}']/following::div[contains(@class,'devui-select-placeholder')][1]",
            f"xpath=//*[normalize-space()='{label}']/following::div[contains(@class,'devui-select-placeholder')][1]",
            f"xpath=//label[contains(normalize-space(),'{label}')]/following::div[contains(@class,'devui-select-placeholder')][1]",
            f"xpath=//span[contains(normalize-space(),'{label}')]/following::div[contains(@class,'devui-select-placeholder')][1]",
            f"xpath=//*[contains(normalize-space(),'{label}')]/following::div[contains(@class,'devui-select-placeholder')][1]",
        ]:
            t = page.locator(xpath)
            if t.count() > 0:
                for i in range(min(t.count(), 3)):
                    try:
                        candidate = t.nth(i)
                        if candidate.is_visible():
                            return candidate
                    except:
                        pass

    # 策略A: label/span/text 后的第一个 input（最精确，label 和 input 在同一表单行内）
    for xpath in [
        f"xpath=//label[text()='{label}']/following::input[1]",
        f"xpath=//span[text()='{label}']/following::input[1]",
        f"xpath=//*[text()='{label}']/following::input[1]",
        f"xpath=//label[contains(text(),'{label}')]/following::input[1]",
        f"xpath=//span[contains(text(),'{label}')]/following::input[1]",
        f"xpath=//*[contains(text(),'{label}')]/following::input[1]",
    ]:
        t = page.locator(xpath)
        if t.count() > 0:
            for i in range(min(t.count(), 3)):
                try:
                    candidate = t.nth(i)
                    if candidate.is_visible():
                        return candidate
                except:
                    pass

    # 策略B: label 所在父级 div 内的第一个 input（适用于 label 和 input 有共同父容器）
    for xpath in [
        f"xpath=//label[text()='{label}']/parent::div//input[1]",
        f"xpath=//span[text()='{label}']/parent::div//input[1]",
        f"xpath=//label[contains(text(),'{label}')]/parent::div//input[1]",
        f"xpath=//span[contains(text(),'{label}')]/parent::div//input[1]",
    ]:
        t = page.locator(xpath)
        if t.count() > 0:
            for i in range(min(t.count(), 3)):
                try:
                    candidate = t.nth(i)
                    if candidate.is_visible():
                        return candidate
                except:
                    pass

    # 策略C: 通过 ancestor 回溯到 form-group/form-item 级别再找 input（兜底）
    for xpath in [
        f"xpath=//label[contains(text(),'{label}')]/ancestor::div[contains(@class,'form-group') or contains(@class,'form-item')][1]//input[1]",
        f"xpath=//*[contains(text(),'{label}')]/ancestor::div[contains(@class,'form-group') or contains(@class,'form-item')][1]//input[1]",
    ]:
        t = page.locator(xpath)
        if t.count() > 0:
            for i in range(min(t.count(), 3)):
                try:
                    candidate = t.nth(i)
                    if candidate.is_visible():
                        return candidate
                except:
                    pass

    # 策略D: 全局搜索可见 input，通过 placeholder 或 value 辅助确认
    # 这一步是最后兜底，通常不需要
    all_inputs = page.locator("input[type='text']")
    for i in range(min(all_inputs.count(), 20)):
        try:
            inp = all_inputs.nth(i)
            if not inp.is_visible():
                continue
            # 检查这个 input 前面是否有匹配的 label
            has_label = inp.evaluate(f"""
                el => {{
                    let prev = el.previousElementSibling;
                    while (prev) {{
                        if (prev.textContent && prev.textContent.includes('{label}')) return true;
                        prev = prev.previousElementSibling;
                    }}
                    let parent = el.parentElement;
                    for (let i = 0; i < 3 && parent; i++) {{
                        if (parent.textContent && parent.textContent.includes('{label}')) return true;
                        parent = parent.parentElement;
                    }}
                    return false;
                }}
            """)
            if has_label:
                return inp
        except:
            pass

    return None


def extract_input_from_trigger(trigger):
    """
    如果 trigger 是 div/span 等容器，尝试从中提取内部的 input。
    返回 (input_locator, 是否成功)。
    """
    try:
        tag = trigger.evaluate("el => el.tagName.toLowerCase()")
    except:
        return trigger, False

    if tag == "input" or tag == "textarea":
        return trigger, True

    for sel in ["input[class*='select-input']", "input[class*='devui-input']", "input"]:
        try:
            inner = trigger.locator(sel)
            if inner.count() > 0:
                for i in range(inner.count()):
                    inp = inner.nth(i)
                    if inp.is_visible():
                        return inp, True
        except:
            continue
    return trigger, False


def clear_select_value_by_label(page, label):
    """
    清空指定选择框中已有的默认值。
    主要用于“提出人”：该字段如果已有默认值，直接点击选择框可能点不开，需要先点内部的小叉。
    """
    try:
        clicked = page.evaluate("""
            (labelText) => {
                const normalize = (s) => (s || '').replace(/\\s+/g, '').trim();
                const isVisible = (el) => {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.display !== 'none' &&
                           style.visibility !== 'hidden' &&
                           style.opacity !== '0' &&
                           rect.width > 0 &&
                           rect.height > 0;
                };
                const clickEl = (el) => {
                    el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
                    el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
                    el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                };

                const labelNodes = Array.from(document.querySelectorAll('label, span, div'))
                    .filter(el => isVisible(el) && normalize(el.textContent) === normalize(labelText));

                for (const labelNode of labelNodes) {
                    const labelRect = labelNode.getBoundingClientRect();
                    const containers = [];

                    // 先在同一表单行/父容器内找 select 容器。
                    let parent = labelNode.parentElement;
                    for (let depth = 0; depth < 6 && parent; depth++, parent = parent.parentElement) {
                        const found = Array.from(parent.querySelectorAll(
                            "d-select, devui-select, [class*='devui-select'], [class*='d-select'], [class*='select']"
                        )).filter(el => {
                            if (!isVisible(el) || el === labelNode) return false;
                            const rect = el.getBoundingClientRect();
                            return rect.right > labelRect.right &&
                                   Math.abs(rect.top - labelRect.top) < 80;
                        });
                        containers.push(...found);
                        if (containers.length > 0) break;
                    }

                    // 父容器内找不到时，找 label 后面的第一个 select 容器。
                    if (containers.length === 0) {
                        let cur = labelNode;
                        for (let i = 0; i < 80 && cur; i++) {
                            cur = cur.nextElementSibling || cur.parentElement?.nextElementSibling;
                            if (!cur) break;
                            if (isVisible(cur) && /select/i.test(cur.className || cur.tagName || '')) {
                                containers.push(cur);
                                break;
                            }
                            const inner = cur.querySelector?.(
                                "d-select, devui-select, [class*='devui-select'], [class*='d-select'], [class*='select']"
                            );
                            if (inner && isVisible(inner)) {
                                containers.push(inner);
                                break;
                            }
                        }
                    }

                    for (const container of containers) {
                        const cRect = container.getBoundingClientRect();

                        // 优先点击带 clear/close/remove/delete 语义的元素或其内部 svg。
                        const semanticCandidates = Array.from(container.querySelectorAll(
                            "[class*='clear'], [class*='close'], [class*='remove'], [class*='delete'], " +
                            "[class*='clear'] svg, [class*='close'] svg, [class*='remove'] svg, [class*='delete'] svg"
                        )).filter(el => {
                            if (!isVisible(el)) return false;
                            const cls = String(el.className || '');
                            if (/arrow|caret|down|dropdown/i.test(cls)) return false;
                            const rect = el.getBoundingClientRect();
                            return rect.left >= cRect.left &&
                                   rect.right <= cRect.right &&
                                   rect.top >= cRect.top &&
                                   rect.bottom <= cRect.bottom;
                        });
                        if (semanticCandidates.length > 0) {
                            clickEl(semanticCandidates[0]);
                            return true;
                        }

                        // 兜底：截图中的小叉是 12x12 的 svg，通常位于下拉箭头左侧。
                        const svgCandidates = Array.from(container.querySelectorAll('svg'))
                            .filter(el => {
                                if (!isVisible(el)) return false;
                                const cls = String(el.className || '');
                                if (/arrow|caret|down|dropdown/i.test(cls)) return false;
                                const rect = el.getBoundingClientRect();
                                const centerX = rect.left + rect.width / 2;
                                return rect.width <= 24 &&
                                       rect.height <= 24 &&
                                       centerX < cRect.right - 28 &&
                                       rect.left >= cRect.left &&
                                       rect.right <= cRect.right &&
                                       rect.top >= cRect.top &&
                                       rect.bottom <= cRect.bottom;
                            })
                            .sort((a, b) => b.getBoundingClientRect().left - a.getBoundingClientRect().left);
                        if (svgCandidates.length > 0) {
                            clickEl(svgCandidates[0]);
                            return true;
                        }
                    }
                }
                return false;
            }
        """, label)

        if clicked:
            print(f"    ✅ {label} 已点击清除小叉")
            page.wait_for_timeout(WAIT_CLEAR_MS if FAST_DROPDOWN_MODE else 500)
            return True
        else:
            print(f"    ⚠ {label} 未找到清除小叉，继续尝试直接展开")
            return False
    except Exception as e:
        print(f"    ⚠ {label} 清除默认值失败: {e}")
        return False


def find_dropdown_panel(page):
    """查找当前展开的下拉面板。返回 panel locator 列表。"""
    panels = []
    for sel in [".devui-dropdown-menu", ".devui-select-dropdown", ".devui-dropdown-panel",
                ".cdk-overlay-pane", ".d-select-dropdown", ".ng-dropdown-panel",
                ".select-dropdown", "[class*='dropdown-panel']", "[class*='select-panel']",
                ".dropdown-menu", ".select-menu"]:
        loc = page.locator(sel)
        for i in range(loc.count()):
            try:
                p = loc.nth(i)
                if p.is_visible():
                    panels.append(p)
            except:
                continue
    return panels


def is_panel_visible(page):
    """检查当前是否有可见的下拉面板。"""
    panels = find_dropdown_panel(page)
    return len(panels) > 0


def find_search_input(page, panels):
    """
    综合多种方式查找搜索框。
    1. 先在下拉面板内查找（按 class / placeholder / type）
    2. 再在页面全局查找
    返回 (input_locator, 是否成功)。
    """
    # 策略1: 在面板内按 class 匹配搜索框
    search_class_selectors = [
        "input[class*='select-search']",
        "input[class*='dropdown-search']",
        "input[class*='search-input']",
        "input.devui-select-search",
    ]
    for p in panels:
        # 快速排除：面板内根本没有 input，跳过所有选择器遍历
        try:
            if p.locator("input").count() == 0:
                continue
        except:
            pass
        for sel in search_class_selectors:
            try:
                inputs = p.locator(sel)
                for j in range(min(inputs.count(), 3)):
                    inp = inputs.nth(j)
                    if inp.is_visible():
                        return inp, True
            except:
                continue

    # 策略2: 在面板内按 placeholder / type 匹配（排除标题栏）
    for p in panels:
        try:
            if p.locator("input").count() == 0:
                continue
        except:
            pass
        for inp_sel in ["input[placeholder*='搜索']", "input[placeholder*='Search']",
                         "input[placeholder*='输入']", "input[type='text']", "input"]:
            try:
                inputs = p.locator(inp_sel)
                for j in range(min(inputs.count(), 3)):
                    inp = inputs.nth(j)
                    if inp.is_visible():
                        try:
                            ph = (inp.get_attribute("placeholder") or "").lower()
                            nm = (inp.get_attribute("name") or "").lower()
                            if any(k in ph or k in nm for k in ["标题", "主题", "title", "subject"]):
                                continue
                        except:
                            pass
                        return inp, True
            except:
                continue

    # 策略3: 页面全局查找
    for sel in search_class_selectors:
        try:
            inputs = page.locator(sel)
            for j in range(min(inputs.count(), 5)):
                inp = inputs.nth(j)
                if inp.is_visible():
                    return inp, True
        except:
            continue

    # 策略4: 全局按 name='select' 查找（华为云特征）
    try:
        inputs = page.locator("input[name='select']")
        for j in range(min(inputs.count(), 3)):
            inp = inputs.nth(j)
            if inp.is_visible():
                return inp, True
    except:
        pass

    return None, False


def find_and_click_option(page, panels, value):
    """
    在下拉面板内查找匹配选项并点击。
    先在面板内搜索，面板找不到则全局搜索。
    返回是否成功。
    """
    # 1. 在面板内精确匹配
    for p in panels:
        try:
            opt = p.get_by_text(value, exact=True)
            if opt.count() > 0:
                opt.first.click()
                page.wait_for_timeout(WAIT_CLICK_MS if FAST_DROPDOWN_MODE else 500)
                return True
        except:
            continue

    # 2. 在面板内模糊匹配
    for p in panels:
        try:
            opt = p.get_by_text(value, exact=False)
            if opt.count() > 0:
                for j in range(opt.count()):
                    o = opt.nth(j)
                    if o.is_visible():
                        o.click()
                        page.wait_for_timeout(WAIT_CLICK_MS if FAST_DROPDOWN_MODE else 500)
                        return True
        except:
            continue

    # 3. 在面板内遍历 li 项
    for p in panels:
        for item_sel in ["li", ".devui-dropdown-item", ".ng-option",
                         ".d-select-item", ".select-item", "[role='option']"]:
            try:
                items = p.locator(item_sel)
                for i in range(items.count()):
                    item = items.nth(i)
                    if item.is_visible() and value in (item.text_content() or ""):
                        item.click()
                        page.wait_for_timeout(WAIT_CLICK_MS if FAST_DROPDOWN_MODE else 500)
                        return True
            except:
                continue

    # 4. 全局精确匹配
    option = page.get_by_text(value, exact=True)
    for i in range(min(option.count(), 20)):
        try:
            opt = option.nth(i)
            if opt.is_visible():
                opt.click()
                page.wait_for_timeout(WAIT_CLICK_MS if FAST_DROPDOWN_MODE else 500)
                return True
        except:
            continue

    # 5. 全局模糊匹配
    option = page.get_by_text(value, exact=False)
    for i in range(min(option.count(), 30)):
        try:
            opt = option.nth(i)
            if opt.is_visible():
                opt.click()
                page.wait_for_timeout(WAIT_CLICK_MS if FAST_DROPDOWN_MODE else 500)
                return True
        except:
            continue

    # 6. 全局遍历 li 项
    for item_sel in ["li", ".devui-dropdown-item", ".ng-option",
                     ".d-select-item", ".select-item", "[role='option']"]:
        items = page.locator(item_sel)
        for i in range(min(items.count(), 80)):
            try:
                item = items.nth(i)
                if item.is_visible() and value in (item.text_content() or ""):
                    item.click()
                    page.wait_for_timeout(WAIT_CLICK_MS if FAST_DROPDOWN_MODE else 500)
                    return True
            except:
                continue

    # 7. JS点击兜底
    option = page.get_by_text(value, exact=True)
    if option.count() > 0:
        if js_click(page, option):
            page.wait_for_timeout(WAIT_CLICK_MS if FAST_DROPDOWN_MODE else 500)
            return True

    return False


def select_by_keyboard(page, trigger, value, max_attempts=30):
    """
    使用键盘导航选择下拉框选项。
    策略：展开 -> 按 Home 回顶部 -> 逐次按 Down 遍历 -> 匹配文本 -> Enter 选中。
    返回是否成功。
    """
    if not is_panel_visible(page):
        try:
            trigger.click()
            wait_until_panel_visible(page, WAIT_RETRY_MS if FAST_DROPDOWN_MODE else 800)
        except:
            pass
    if not is_panel_visible(page):
        print(f"    ⚠ 面板无法展开，无法使用键盘导航")
        return False

    print(f"    🎹 键盘导航选择: {value}")
    page.keyboard.press("Home")
    page.wait_for_timeout(80 if FAST_DROPDOWN_MODE else 200)

    for i in range(max_attempts):
        page.keyboard.press("ArrowDown")
        page.wait_for_timeout(80 if FAST_DROPDOWN_MODE else 300)

        current_text = ""
        try:
            active_items = page.locator(
                ".devui-dropdown-item.active, .devui-dropdown-item.current, "
                ".ng-option-marked, [class*='active'][role='option'], "
                ".devui-dropdown-item:hover, .ng-option-selected"
            )
            if active_items.count() > 0:
                current_text = (active_items.first.text_content() or "").strip()
        except:
            pass

        if not current_text:
            try:
                panels = find_dropdown_panel(page)
                for p in panels:
                    items = p.locator("li, .devui-dropdown-item, .ng-option, [role='option']")
                    for j in range(items.count()):
                        item = items.nth(j)
                        cls = item.evaluate("el => el.className") or ""
                        if "active" in cls or "current" in cls or "marked" in cls or "selected" in cls:
                            current_text = (item.text_content() or "").strip()
                            break
                    if current_text:
                        break
            except:
                pass

        print(f"      [{i+1}] 当前项: {current_text}")

        if value in current_text or current_text == value:
            page.keyboard.press("Enter")
            page.wait_for_timeout(WAIT_CLICK_MS if FAST_DROPDOWN_MODE else 500)
            print(f"    ✅ 键盘选中: {value}")
            return True

    print(f"    ⚠ 键盘导航未找到: {value}")
    return False


# ================== 核心：通过搜索框输入定位选项 ==================
def safe_select_dropdown(page, label, value):
    """
    通过 label 文本定位下拉框，展开后智能选择选项。
    V12核心修复：
      1. 每次开始前用 JS 强制关闭所有面板，彻底避免上一个面板干扰
      2. get_trigger_by_label 改为 following::input 精确定位
      3. is_devui_select 只检测 devui-select-input，避免普通 input 误触发
      4. 填完后再次强制关闭面板
    """
    if not value:
        return True

    # 先强制关闭所有面板（JS display:none，最可靠）
    close_all_dropdowns(page)

    # “提出人”如果已有默认值，直接点击选择框可能无法展开，需要先点字段内的小叉清空。
    if label == "提出人":
        clear_select_value_by_label(page, label)
        close_all_dropdowns(page)

    # ---------- 1. 定位下拉触发器 ----------
    trigger = get_trigger_by_label(page, label)
    if trigger is None:
        print(f"    ⚠ 未找到下拉框: {label}")
        return False

    trigger, is_input = extract_input_from_trigger(trigger)

    # 检查当前值是否已经是目标值
    try:
        current_text = (trigger.input_value() or "").strip()
        if current_text == "":
            current_text = (trigger.text_content() or "").strip()
        if value in current_text or current_text == value:
            print(f"    ✅ {label} -> {value} (已是目标值，跳过)")
            return True
    except:
        pass

    # ---------- 2. 点击展开下拉列表 ----------
    print(f"    🔄 处理: {label} -> {value}")
    try:
        # bug复现率在页面底部，Playwright 平滑滚动很慢，用 JS instant 滚动+点击加速
        if label == "bug复现率":
            trigger.evaluate("el => { el.scrollIntoView({block:'center', behavior:'instant'}); el.click(); }")
            page.wait_for_timeout(80 if FAST_DROPDOWN_MODE else 200)
        else:
            trigger.scroll_into_view_if_needed()
            trigger.click()
        wait_until_panel_visible(page)
    except Exception as e:
        print(f"    ⚠ 点击下拉框失败: {e}，尝试JS点击...")
        if not js_click(page, trigger):
            return False
        wait_until_panel_visible(page)

    # 检查面板是否真正展开
    if not is_panel_visible(page):
        print(f"    ⚠ 面板未展开，重试点击...")
        try:
            if label == "bug复现率":
                trigger.evaluate("el => el.click()")
                page.wait_for_timeout(80 if FAST_DROPDOWN_MODE else 200)
            else:
                trigger.click()
            wait_until_panel_visible(page, WAIT_RETRY_MS if FAST_DROPDOWN_MODE else 1200)
        except:
            pass
        if not is_panel_visible(page):
            print(f"    ⚠ 面板始终无法展开: {label}")
            close_all_dropdowns(page)
            return False

    print(f"    📂 面板已展开")
    panels = find_dropdown_panel(page)

    # ---------- 3a. 选项少的字段优先用键盘导航（比搜索+点击更快） ----------
    if label in KEYBOARD_FIRST_LABELS:
        print(f"    🎹 {label} 选项少，优先尝试键盘导航...")
        if select_by_keyboard(page, trigger, value, max_attempts=15):
            close_all_dropdowns(page)
            return True
        print(f"    ⚠ 键盘导航失败，回退到搜索+点击...")

    # ---------- 3b. bug复现率选项少且无搜索框，直接尝试点击选项，跳过 find_search_input 耗时 ----------
    if label == "bug复现率" and is_panel_visible(page):
        if find_and_click_option(page, panels, value):
            close_all_dropdowns(page)
            page.wait_for_timeout(WAIT_CLICK_MS if FAST_DROPDOWN_MODE else 300)
            try:
                current_text = (trigger.input_value() or "").strip()
                if current_text == "":
                    current_text = (trigger.text_content() or "").strip()
                if value in current_text or current_text == value:
                    print(f"    ✅ {label} -> {value} (已选中)")
                    return True
            except:
                pass
            print(f"    ✅ {label} -> {value} (点击成功)")
            return True
        print(f"    ⚠ {label} 直接点击失败，回退到搜索流程...")

    # ---------- 3c. 查找搜索框并输入 ----------
    search_input, has_search = find_search_input(page, panels)
    input_success = False

    if has_search and search_input is not None:
        print(f"    🔍 找到搜索框，尝试输入...")
        try:
            search_input.click()
            page.wait_for_timeout(60 if FAST_DROPDOWN_MODE else 200)
            if not fast_fill_input(search_input, value):
                raise Exception("搜索框 fill/type 均失败")
            page.wait_for_timeout(WAIT_SEARCH_MS if FAST_DROPDOWN_MODE else 1500)
            print(f"    📝 搜索框已输入: {value}")
            input_success = True
            panels = find_dropdown_panel(page)
        except Exception as e:
            print(f"    ⚠ 搜索框输入失败: {e}")

    if not input_success:
        # 尝试在触发器上输入（仅限 devui-select-input，不检测 devui-input）
        try:
            tag = trigger.evaluate("el => el.tagName.toLowerCase()")
            cls = trigger.get_attribute("class") or ""
            # V12 只检测 devui-select-input，避免普通 devui-input 误触发
            if tag == "input" and "devui-select-input" in cls:
                print(f"    📝 在触发器输入: {value}")
                if not fast_fill_input(trigger, value):
                    raise Exception("触发器 fill/type 均失败")
                page.wait_for_timeout(WAIT_SEARCH_MS if FAST_DROPDOWN_MODE else 1500)
                input_success = True
                panels = find_dropdown_panel(page)
        except Exception as e:
            print(f"    ⚠ 触发器输入失败: {e}")

    if not input_success:
        print(f"    📝 无有效搜索框，尝试直接选择选项...")

    # ---------- 4. 点击选中匹配选项 ----------
    if is_panel_visible(page):
        if find_and_click_option(page, panels, value):
            close_all_dropdowns(page)
            page.wait_for_timeout(WAIT_CLICK_MS if FAST_DROPDOWN_MODE else 300)
            try:
                current_text = (trigger.input_value() or "").strip()
                if current_text == "":
                    current_text = (trigger.text_content() or "").strip()
                if value in current_text or current_text == value:
                    print(f"    ✅ {label} -> {value} (已选中)")
                    return True
            except:
                pass
            print(f"    ✅ {label} -> {value} (点击成功)")
            return True

    # ---------- 5. 鼠标点击失败，尝试键盘导航 ----------
    print(f"    🎹 鼠标点击失败，尝试键盘导航...")
    if not is_panel_visible(page):
        try:
            trigger.click()
            wait_until_panel_visible(page, WAIT_RETRY_MS if FAST_DROPDOWN_MODE else 800)
        except:
            pass

    if is_panel_visible(page):
        if select_by_keyboard(page, trigger, value):
            close_all_dropdowns(page)
            return True

    # ---------- 6. 最终检查 ----------
    page.wait_for_timeout(WAIT_CLICK_MS if FAST_DROPDOWN_MODE else 300)
    try:
        current_text = (trigger.input_value() or "").strip()
        if current_text == "":
            current_text = (trigger.text_content() or "").strip()
        if value in current_text or current_text == value:
            close_all_dropdowns(page)
            print(f"    ✅ {label} -> {value} (已选中)")
            return True
    except:
        pass

    print(f"    ⚠ 未找到选项: {label} -> {value}")
    close_all_dropdowns(page)
    return False


# ================== 表单填写 ==================
def fill_bug_form(page, title, description):
    """在新建Bug界面填写所有字段"""
    ok = True

    # ---- 填写标题 ----
    title_filled = False
    for kw in ["标题", "主题", "Bug标题", "请输入标题"]:
        loc = page.locator(f'input[placeholder*="{kw}"], textarea[placeholder*="{kw}"]')
        if loc.count() > 0:
            loc.first.fill(title)
            title_filled = True
            break
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
        ok = False
    else:
        print(f"    ✅ 标题已填写")

    # ---- 填写描述 ----
    desc_filled = False
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
        ok = False
    else:
        print(f"    ✅ 描述已填写")

    # ---- 自动填写下拉列表 ----
    if AUTO_DROPDOWNS and DEFAULT_VALUES:
        print("    🔄 开始填写下拉列表...")
        for label, value in DEFAULT_VALUES.items():
            if value:
                safe_select_dropdown(page, label, value)
        print("    🔄 下拉列表填写完毕")

    return ok


# ================== 保存 ==================
def try_save_bug(page):
    """尝试点击保存按钮"""
    save_btn = None
    for text in ["保存", "提交", "确定", "创建", "Save", "Create"]:
        loc = page.get_by_role("button", name=text, exact=True)
        if loc.count() == 0:
            loc = page.get_by_text(text, exact=True)
        if loc.count() > 0:
            save_btn = loc.first
            break
    if save_btn is None:
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
    """回到缺陷列表页"""
    bug_tab = page.locator("a.devui-header-dynamic-link:has-text('缺陷管理')")
    if bug_tab.count() == 0:
        bug_tab = page.locator("a").filter(has_text="缺陷管理")
    if bug_tab.count() == 0:
        bug_tab = page.locator("a").filter(has_text="缺陷")
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


# ================== 主流程 ==================
def main():
    if not os.path.exists(BUG_TXT_PATH):
        print(f"❌ 文件不存在: {BUG_TXT_PATH}")
        return

    bugs = parse_bugs(BUG_TXT_PATH)
    total = len(bugs)
    print(f"📄 共解析到 {total} 条Bug记录")
    print(f"📋 下拉框默认值配置: {DEFAULT_VALUES}")
    print(f"🔧 AUTO_DROPDOWNS={AUTO_DROPDOWNS}, AUTO_SAVE={AUTO_SAVE}\n")

    if total == 0:
        print("❌ 未解析到任何记录")
        return

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=USER_DATA_DIR,
            channel="msedge",
            headless=False,
            args=["--start-maximized"]
        )
        page = context.new_page()

        # Step 1: IAM 启动台
        page.goto("https://iam.navinfo.com/6528b3cd46fed68caf998b66/launchpad")
        page.wait_for_load_state("networkidle")
        print("Step 1: 启动台加载完成")

        # Step 2: 点击华为云
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

        # Step 3: 进入项目
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

        # Step 4: 点击缺陷管理
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

        # 逐条处理
        for idx, bug in enumerate(bugs, start=1):
            print(f"\n{'='*60}")
            print(f"【{idx}/{total}】{bug['title'][:60]}...")
            print(f"{'='*60}")

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
                    new_bug = page.get_by_text("新建Bug")
                if new_bug.count() == 0:
                    print("  ❌ 仍然未找到'新建Bug'，跳过本条")
                    continue

            new_bug.first.click()
            print("  -> 已打开新建Bug界面")
            page.wait_for_timeout(3000)

            ok = fill_bug_form(page, bug["title"], bug["description"])
            if ok:
                print("  ✅ 表单填写完成")
                page.screenshot(path=f"bug_filled_{idx:03d}.png")
            else:
                print("  ⚠ 填写可能不完整")
                page.screenshot(path=f"bug_failed_{idx:03d}.png")

            if AUTO_SAVE:
                try_save_bug(page)
                page.wait_for_timeout(3000)
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
