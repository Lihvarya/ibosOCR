# -*- coding: utf-8 -*-
import json
import re
import os
import glob
from bs4 import BeautifulSoup

# --- Config ---
HTML_DIR = '.'
OUTPUT_FILE = 'combined_questions_data.json'


# ==========================================
# 核心工具函数
# ==========================================

def clean_text(text):
    """基础清洗：去空白、去特定前缀"""
    if not text: return ""
    # 统一标点符号，避免中文冒号和英文冒号导致的不一致
    text = text.replace('：', ':').replace('，', ',').replace('？', '?').replace('（', '(').replace('）', ')')
    text = re.sub(r'\s+', ' ', text).strip()
    # 去除选项前缀如 "A. " 或 "A、"
    text = re.sub(r'^[A-Z][\.\、]\s*', '', text)
    text = re.sub(r'^本题解析\s*[:]\s*', '', text)
    if text in ['【无】', '无', '']:
        return ""
    return text


def generate_content_fingerprint(text):
    """
    生成题目内容的唯一指纹。
    逻辑：去除所有标点符号、空格、特殊字符，只保留汉字和字母数字，并转小写。
    例如："1. 什么是 TCP/IP？" -> "什么是tcpip"
    """
    if not text: return ""
    # 1. 去除前面的序号 (如 "12. ")
    text = re.sub(r'^\d+\s*[\.、]\s*', '', text)
    # 2. 去除题型标记 (如 "【单选】")
    text = re.sub(r'【.*?】', '', text)
    # 3. 只保留汉字、字母、数字
    clean = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9]', '', text)
    return clean.lower()


def is_valid_value(val):
    """判断一个值是否有效（非空、非无、非占位符）"""
    if not val: return False
    if isinstance(val, str):
        s = val.strip()
        return s and s not in ["无", "【无】", "未知", "未作答", "未提供"]
    return True


def merge_questions(old_q, new_q):
    """
    合并两个重复的题目，保留信息量最大的字段。
    """
    # 1. 优先保留有解析的
    if not is_valid_value(old_q.get('analysis')) and is_valid_value(new_q.get('analysis')):
        old_q['analysis'] = new_q['analysis']

    # 2. 优先保留有正确答案的
    if not is_valid_value(old_q.get('correct_answer')) and is_valid_value(new_q.get('correct_answer')):
        old_q['correct_answer'] = new_q['correct_answer']

    # 3. 优先保留有选项的
    if not old_q.get('options') and new_q.get('options'):
        old_q['options'] = new_q['options']

    # 4. 来源库名合并 (方便知道这道题出现在哪些库里)
    if new_q['bank_name'] not in old_q['bank_name']:
        old_q['bank_name'] += f" / {new_q['bank_name']}"

    return old_q


# ==========================================
# 解析逻辑 (保持之前的双重策略)
# ==========================================

def parse_structured_html(soup, bank_name):
    """策略A: 解析结构良好的HTML"""
    questions = []
    items = soup.find_all('div', class_='question-item')
    for item in items:
        try:
            q = {'bank_name': bank_name}
            type_tag = item.select_one('.question-item__type')
            if type_tag:
                raw_type = clean_text(type_tag.get_text())
                m = re.match(r'(\d+)\s*[.、]\s*【(.*?)】', raw_type)
                if m:
                    q['number'] = int(m.group(1))
                    q['type'] = m.group(2)
                else:
                    q['type'] = raw_type

            content_tag = item.select_one('.question-item__content')
            q['text'] = clean_text(content_tag.get_text(separator='\n')) if content_tag else ""
            q['options'] = [clean_text(li.get_text()) for li in item.select('.question-item__option li')]

            ans_div = item.select_one('.stu-answer')
            q['student_answer'], q['correct_answer'] = "未作答", "未知"
            if ans_div:
                txt = ans_div.get_text()
                m_stu = re.search(r'我的答案[:：]\s*(\S+)', txt)
                if m_stu: q['student_answer'] = m_stu.group(1).strip()
                m_cor = re.search(r'正确答案[:：]\s*(\S+)', txt)
                if m_cor: q['correct_answer'] = m_cor.group(1).strip()

            analysis_div = item.select_one('.analysis')
            if analysis_div:
                content_div = analysis_div.select_one('.analysis-content')
                q['analysis'] = clean_text(content_div.get_text()) if content_div else clean_text(
                    analysis_div.get_text()).replace('本题解析:', '')
            else:
                q['analysis'] = "无"
            questions.append(q)
        except:
            continue
    return questions


def parse_plain_text_html(soup, bank_name):
    """策略B: 解析纯文本 HTML"""
    questions = []
    text_content = soup.get_text(separator='\n')
    lines = [line.strip() for line in text_content.split('\n') if line.strip()]

    current_q = None
    state = 'SEARCHING'

    # 正则 (适配 "1.【单选】" 或 "1. 题目")
    re_start = re.compile(r'^(\d+)\s*[.、]\s*(?:【(.*?)】)?\s*(.*)')
    re_option = re.compile(r'^([A-Z])\s*[.、]\s*(.*)')
    re_answer = re.compile(r'我的答案[:：]\s*(.*?)\s*正确答案[:：]\s*(.*)')
    re_analysis = re.compile(r'^本题解析\s*[:：]\s*(.*)')

    for line in lines:
        m_start = re_start.match(line)
        if m_start:
            if current_q: questions.append(current_q)
            q_type = m_start.group(2) if m_start.group(2) else "题目"
            q_text_start = m_start.group(3) if m_start.group(3) else ""
            current_q = {
                'bank_name': bank_name,
                'number': int(m_start.group(1)),
                'type': q_type,
                'text': q_text_start + "\n" if q_text_start else "",
                'options': [], 'student_answer': '未作答', 'correct_answer': '未知', 'analysis': '无'
            }
            state = 'READING_TEXT'
            continue

        if not current_q: continue

        m_opt = re_option.match(line)
        if m_opt and state != 'FINISHED':
            state = 'READING_OPTIONS'
            opt_content = m_opt.group(2).strip()
            if not opt_content and len(line) > 2: opt_content = line[2:].strip()
            current_q['options'].append(opt_content)
            continue

        m_ans = re_answer.search(line)
        if m_ans:
            current_q['student_answer'] = m_ans.group(1).strip()
            current_q['correct_answer'] = m_ans.group(2).strip()
            state = 'FINISHED'
            continue

        m_ana = re_analysis.match(line)
        if m_ana:
            current_q['analysis'] = m_ana.group(1).strip() or "无"
            state = 'FINISHED'
            continue

        if state == 'READING_TEXT':
            current_q['text'] += line + "\n"
        elif state == 'READING_OPTIONS' and current_q['options']:
            current_q['options'][-1] += " " + line

    if current_q: questions.append(current_q)
    for q in questions: q['text'] = q['text'].strip()
    return questions


def parse_html_file(filepath):
    """调度器"""
    bank_name = os.path.splitext(os.path.basename(filepath))[0]
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            soup = BeautifulSoup(f, 'lxml')
        qs = parse_structured_html(soup, bank_name)
        if not qs:
            qs = parse_plain_text_html(soup, bank_name)
        return qs
    except Exception as e:
        print(f"解析失败 {filepath}: {e}")
        return []


# ==========================================
# 主程序：去重与合并
# ==========================================

def load_json(filepath):
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return []


def main():
    print("=== 启动智能题库合并与去重 ===")

    # 1. 收集所有原始数据 (旧数据 + 新HTML数据)
    all_raw_questions = []

    # 加载旧 JSON
    old_data = load_json(OUTPUT_FILE)
    print(f"已加载历史数据: {len(old_data)} 条")
    all_raw_questions.extend(old_data)

    # 加载新 HTML
    html_files = glob.glob(os.path.join(HTML_DIR, '*.html'))
    new_count = 0
    for f in html_files:
        if 'search_questions.html' in f: continue
        qs = parse_html_file(f)
        if qs:
            print(f"  - 从 {os.path.basename(f)} 提取到 {len(qs)} 条")
            all_raw_questions.extend(qs)
            new_count += len(qs)

    print(f"待处理数据总数: {len(all_raw_questions)} 条 (历史 + 新增)")

    # 2. 核心去重逻辑
    unique_map = {}  # Key: 题目指纹, Value: 题目对象
    duplicates_found = 0

    print("正在执行智能合并去重...")
    for q in all_raw_questions:
        # 生成指纹 (只基于题目内容)
        fingerprint = generate_content_fingerprint(q.get('text', ''))

        # 指纹太短可能是解析错误或空题，直接跳过或作为独立题处理
        if len(fingerprint) < 2:
            # 如果指纹太短，尝试加上选项指纹，防止误判
            fingerprint += "".join([generate_content_fingerprint(o) for o in q.get('options', [])])

        if not fingerprint: continue

        if fingerprint in unique_map:
            # 发现重复！执行合并策略
            existing_q = unique_map[fingerprint]
            merged_q = merge_questions(existing_q, q)
            unique_map[fingerprint] = merged_q
            duplicates_found += 1
        else:
            # 新题目
            unique_map[fingerprint] = q

    # 3. 转换为列表并排序
    final_data = list(unique_map.values())
    # 按题库名和题目长度排序（通常长度长的题比较复杂）
    final_data.sort(key=lambda x: (x.get('bank_name', ''), len(x.get('text', ''))))

    # 4. 保存
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(final_data, f, ensure_ascii=False, indent=2)

    print("-" * 30)
    print(f"去重合并完成！")
    print(f"  - 发现重复并合并: {duplicates_found} 条")
    print(f"  - 最终有效题目库: {len(final_data)} 条")
    print(f"数据已保存至: {OUTPUT_FILE}")
    print("-" * 30)
    print("提示：如果网页搜索不到新题，请在浏览器中按 Ctrl+F5 强制刷新。")


if __name__ == "__main__":
    main()