# -*- coding: utf-8 -*-
import json
import re
import os
import glob  # 用于查找文件
from bs4 import BeautifulSoup

# --- Functions from extract_questions.py ---
def clean_text(text):
    """去除多余空白和特定样板文本。"""
    if text is None:
        return ""
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'^[A-Z]\.\s*', '', text) # 处理 A., B., C., D., E. 前缀
    text = re.sub(r'^本题解析\s*：', '', text).strip()
    if text == '【无】':
        return ""
    return text

def get_bank_name_from_filepath(filepath):
    """从HTML文件名生成可读的题库名称。"""
    base_name = os.path.basename(filepath)
    name_part = os.path.splitext(base_name)[0]
    return name_part if name_part else "未知题库"

def extract_questions_from_html(html_filepath, bank_name):
    """从单个HTML文件提取问题数据，并标记题库名称。"""
    extracted_data = []
    print(f"   正在处理 HTML 文件: {html_filepath} (题库: {bank_name})")
    if not os.path.exists(html_filepath):
        print(f"   错误: 文件未找到。")
        return []
    try:
        with open(html_filepath, 'r', encoding='utf-8') as f:
            html_content = f.read()
    except Exception as e:
        print(f"   读取文件时出错: {e}")
        return []

    soup = BeautifulSoup(html_content, 'lxml')
    question_items = soup.find_all('div', class_='question-item')
    print(f"   找到 {len(question_items)} 个潜在问题项。")

    for item in question_items:
        question_data = {'bank_name': bank_name}
        try:
            question_data['id'] = item.get('id', f'unknown_{bank_name}_{len(extracted_data)}')
            type_tag = item.find('p', class_='question-item__type')
            q_num, q_type = None, "未知类型"
            if type_tag:
                type_text = clean_text(type_tag.get_text(strip=True))
                match = re.match(r'(\d+)\.【(\S+)】', type_text)
                if match:
                    q_num, q_type = int(match.group(1)), match.group(2)
                else:
                    num_match = re.match(r'(\d+)\.', type_text)
                    if num_match:
                        q_num = int(num_match.group(1))
                    q_type = type_text
            question_data['number'] = q_num
            question_data['type'] = q_type
            content_tag = item.find('div', class_='question-item__content')
            question_data['text'] = clean_text(content_tag.get_text(separator='\n', strip=True)) if content_tag else "内容未找到"
            options_list = []
            options_ul = item.find('ul', class_='question-item__option')
            if options_ul:
                options_li = options_ul.find_all('li')
                for li in options_li:
                     option_full_text = clean_text(li.get_text(strip=True))
                     options_list.append(option_full_text)
            question_data['options'] = options_list
            answer_div = item.find('div', class_='stu-answer')
            student_answer, correct_answer = "未提供", "未提供"
            if answer_div:
                student_ans_span = answer_div.find('span', string=lambda t: t and '我的答案：' in t)
                if student_ans_span and student_ans_span.find('b'):
                     student_answer = clean_text(student_ans_span.find('b').get_text(strip=True))
                correct_ans_span = answer_div.find('span', class_='true-answer')
                if correct_ans_span and correct_ans_span.find('b'):
                    correct_answer = clean_text(correct_ans_span.find('b').get_text(strip=True))
                elif correct_ans_span:
                    correct_answer_raw = clean_text(correct_ans_span.get_text(strip=True))
                    match_ans = re.search(r'正确答案：(.*)', correct_answer_raw)
                    correct_answer = match_ans.group(1).strip() if match_ans else correct_answer_raw
            question_data['student_answer'] = student_answer
            question_data['correct_answer'] = correct_answer
            analysis_tag = item.find('div', class_='analysis')
            analysis_text = ""
            if analysis_tag:
                analysis_content = analysis_tag.find('div', class_='analysis-content')
                if analysis_content:
                     analysis_text = clean_text(analysis_content.get_text(separator='\n', strip=True))
                else:
                     base_text_tag = analysis_tag.find('p')
                     if base_text_tag and "本题解析" in base_text_tag.get_text():
                        analysis_span = base_text_tag.find('span')
                        if analysis_span:
                            analysis_text = clean_text(analysis_span.get_text(strip=True))
                        else:
                            analysis_text = clean_text(base_text_tag.get_text(strip=True))
                            analysis_text = re.sub(r'^本题解析\s*：【无】', '', analysis_text).strip()
                     else:
                          analysis_text = clean_text(analysis_tag.get_text(separator='\n', strip=True))
                          analysis_text = re.sub(r'本题解析.*$', '', analysis_text).strip()
            question_data['analysis'] = analysis_text if analysis_text else "无"
            extracted_data.append(question_data)
        except Exception as e:
            print(f"   处理问题项时出错 ({bank_name} - ID: {item.get('id', 'N/A')}): {e}")
    print(f"   从该文件成功提取 {len(extracted_data)} 个问题。")
    return extracted_data

# --- Function for reading existing JSON data ---
def load_existing_json(filepath):
    """如果存在且有效，则从文件加载现有JSON数据。"""
    if os.path.exists(filepath):
        print(f"\n正在尝试从 {filepath} 加载现有数据...")
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    print(f"  成功加载 {len(data)} 条现有数据。")
                    return data
                else:
                    print(f"  警告: 现有文件 {filepath} 的顶层结构不是列表。将从空数据开始。")
                    return []
        except json.JSONDecodeError:
            print(f"  错误: 现有文件 {filepath} 包含无效的 JSON 格式。将从空数据开始。")
            return []
        except Exception as e:
            print(f"  加载现有文件 {filepath} 时出错: {e}。将从空数据开始。")
            return []
    else:
        print(f"\n未在 {filepath} 找到现有文件。将从空数据开始。")
        return []

# --- Function for merging and deduplicating data ---
def merge_and_deduplicate_data(all_data, output_file):
    """合并并去重一个JSON对象列表，保存到输出文件。"""
    if not all_data:
        print("  没有数据可供处理（合并或去重）。跳过保存步骤。")
        # 如果需要，可以删除空输出文件
        # if os.path.exists(output_file):
        #    os.remove(output_file)
        #    print(f"  已移除空输出文件: {output_file}")
        return

    print("-" * 20)
    print(f"开始对总共 {len(all_data)} 条数据进行去重...")

    unique_data = []
    seen_signatures = set()
    duplicates_count = 0

    # 按照 'bank_name' 和 'number' 排序数据，以便处理过程一致且输出更易读
    # 处理 'number' 可能为 None 的情况
    sorted_data = sorted(all_data, key=lambda x: (x.get('bank_name', ''), x.get('number') if x.get('number') is not None else float('inf')))

    for item in sorted_data:
        if not isinstance(item, dict):
            print(f"    警告: 检测到非字典类型条目，已跳过: {type(item)}")
            continue
        try:
            # 为每个字典项生成一个稳定的签名
            # sort_keys=True 确保键的顺序一致，相同内容的字典生成相同的字符串
            # ensure_ascii=False 确保中文字符等正确处理
            signature = json.dumps(item, sort_keys=True, ensure_ascii=False)

            if signature not in seen_signatures:
                seen_signatures.add(signature)
                unique_data.append(item)
            else:
                duplicates_count += 1
        except TypeError as e:
            print(f"    错误: 无法序列化条目进行去重检查 (可能包含不支持的类型)，已跳过: {item} - 错误: {e}")

    print(f"去重完成。移除了 {duplicates_count} 条完全重复的数据。")
    print(f"最终唯一数据数量: {len(unique_data)} 条。")

    # --- 写入输出文件 ---
    try:
        # 使用 'w' 模式覆盖写入文件，包含最新的唯一数据
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(unique_data, f, indent=4, ensure_ascii=False)
        print(f"成功将 {len(unique_data)} 条唯一数据写入到文件: {output_file}")
    except Exception as e:
        print(f"写入输出文件 {output_file} 时出错: {e}")


# --- Main Execution - Combined ---
if __name__ == "__main__":
    # --- 配置区域 ---
    # 包含HTML题库文件的目录
    # '.' 表示当前目录，也可以指定具体路径，如 'C:/Users/YourUser/Documents/QuizFiles'
    html_source_directory = '.'

    # 合并和去重后的最终JSON输出文件
    output_json_file = 'combined_questions_data.json'
    # --- 配置结束 ---

    print("--- 启动 HTML 提取、JSON 合并与去重组合脚本 ---")

    # --- 1. 从 HTML 文件提取数据 ---
    newly_extracted_questions = []
    # 查找指定目录下的所有 .html 文件
    html_files = glob.glob(os.path.join(html_source_directory, '*.html'))

    if not html_files:
        print(f"未在目录 {os.path.abspath(html_source_directory)} 中找到 .html 文件。跳过 HTML 提取。")
    else:
        print(f"找到 {len(html_files)} 个 HTML 文件，开始提取...")
        for html_file in html_files:
             # 如果是已知的搜索页面，则跳过
             if os.path.basename(html_file) == 'search_questions.html':
                 print(f"--- 跳过已知的搜索页面: {html_file} ---")
                 continue
             bank_name = get_bank_name_from_filepath(html_file)
             questions_from_file = extract_questions_from_html(html_file, bank_name)
             if questions_from_file:
                 newly_extracted_questions.extend(questions_from_file)

        print("-" * 20)
        if newly_extracted_questions:
            print(f"从 HTML 中提取了 {len(newly_extracted_questions)} 条新问题数据。")
        else:
            print("没有从任何 HTML 文件中提取到新问题。")

    # --- 2. 加载目标 JSON 文件中的现有数据 ---
    existing_data = load_existing_json(output_json_file)

    # --- 3. 合并新数据和现有数据 ---
    all_data_for_processing = existing_data + newly_extracted_questions
    print(f"\n合并了现有数据 ({len(existing_data)}) 和新提取的数据 ({len(newly_extracted_questions)})，总共 {len(all_data_for_processing)} 条数据。")

    # --- 4. 对合并后的数据进行去重并保存 ---
    merge_and_deduplicate_data(all_data_for_processing, output_json_file)

    print("--- 组合脚本执行完毕 ---")
