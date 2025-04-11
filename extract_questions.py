# -*- coding: utf-8 -*-
import json
import re
import os
import glob # To find files matching a pattern
from bs4 import BeautifulSoup

# --- Functions from extract_questions.py ---
def clean_text(text):
    """Removes excessive whitespace and specific boilerplate."""
    if text is None:
        return ""
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'^[A-Z]\.\s*', '', text) # Handles A., B., C., D., E. prefixes
    text = re.sub(r'^本题解析\s*：', '', text).strip()
    if text == '【无】':
        return ""
    return text

def get_bank_name_from_filepath(filepath):
    """Generates a readable bank name from the HTML filename."""
    base_name = os.path.basename(filepath)
    name_part = os.path.splitext(base_name)[0]
    return name_part if name_part else "Unknown Bank"

def extract_questions_from_html(html_filepath, bank_name):
    """Extracts question data from a single HTML file and tags it with the bank name."""
    extracted_data = []
    print(f"   Processing HTML: {html_filepath} (Bank: {bank_name})")
    if not os.path.exists(html_filepath):
        print(f"   Error: File not found.")
        return []
    try:
        with open(html_filepath, 'r', encoding='utf-8') as f:
            html_content = f.read()
    except Exception as e:
        print(f"   Error reading file: {e}")
        return []

    soup = BeautifulSoup(html_content, 'lxml')
    question_items = soup.find_all('div', class_='question-item')
    print(f"   Found {len(question_items)} potential questions.")

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
            print(f"   Error processing question in {bank_name} (ID: {item.get('id', 'N/A')}): {e}")
    print(f"   Extracted {len(extracted_data)} questions from this file.")
    return extracted_data

# --- Functions from merge_json.py, adapted ---
def merge_and_deduplicate_json_data(all_data, output_file):
    """Merges and deduplicates a list of JSON objects, saves to output file."""
    if not all_data:
        print("  没有数据可以合并或去重，跳过 JSON 合并步骤。")
        return

    print("-" * 20)
    print(f"准备对总共 {len(all_data)} 条数据进行去重...")

    unique_data = []
    seen_signatures = set()
    duplicates_count = 0

    for item in all_data:
        if not isinstance(item, dict):
            print(f"    警告: 检测到非字典类型的条目，已跳过: {type(item)}")
            continue
        try:
            signature = json.dumps(item, sort_keys=True, ensure_ascii=False)
            if signature not in seen_signatures:
                seen_signatures.add(signature)
                unique_data.append(item)
            else:
                duplicates_count += 1
        except TypeError as e:
            print(f"    错误: 无法序列化条目进行重复检查，已跳过: {item} - 错误: {e}")

    print(f"去重完成，共移除了 {duplicates_count} 条完全重复的数据。")
    print(f"最终唯一数据共 {len(unique_data)} 条。")

    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(unique_data, f, indent=4, ensure_ascii=False)
        print(f"成功将 {len(unique_data)} 条唯一数据写入到文件: {output_file}")
    except Exception as e:
        print(f"错误: 写入输出文件 {output_file} 时发生错误: {e}")


# --- Main Execution - Combined ---
if __name__ == "__main__":
    html_source_directory = '.'
    output_json_file = 'combined_questions_data.json'

    print("--- Starting Combined HTML Extraction and JSON Merge Script ---")

    # --- 1. HTML Extraction and Initial JSON Save ---
    all_extracted_questions = []
    html_files = glob.glob(os.path.join(html_source_directory, '*.html'))

    if not html_files:
        print(f"No .html files found in directory: {os.path.abspath(html_source_directory)}")
    else:
        print(f"Found {len(html_files)} HTML files, starting extraction...")
        for html_file in html_files:
             if os.path.basename(html_file) == 'search_questions.html':
                 print(f"--- Skipping search page: {html_file} ---")
                 continue
             bank_name = get_bank_name_from_filepath(html_file)
             questions_from_file = extract_questions_from_html(html_file, bank_name)
             if questions_from_file:
                 all_extracted_questions.extend(questions_from_file)

        print("-" * 20)
        if all_extracted_questions:
            try:
                print(f"Saving extracted questions from HTML to {output_json_file}...")
                with open(output_json_file, 'w', encoding='utf-8') as f:
                    json.dump(all_extracted_questions, f, indent=4, ensure_ascii=False)
                print(f"Successfully saved {len(all_extracted_questions)} questions from HTML to {output_json_file}")
            except Exception as e:
                print(f"Error writing initial JSON file {output_json_file}: {e}")
        else:
            print("No questions were extracted from any HTML file.")

    # --- 2. JSON Merge and Deduplication ---
    print("\n--- Starting JSON Merge and Deduplication Phase ---")

    json_file_pattern = '*.json'  # Merge all json files including the newly created one
    all_json_data_for_merge = []

    json_files_for_merge = glob.glob(json_file_pattern)
    if not json_files_for_merge:
        print(f"Warning: No JSON files found for merging based on pattern '{json_file_pattern}'. Skipping merge step.")
    else:
        print(f"Found {len(json_files_for_merge)} JSON files for merging...")
        for json_file in json_files_for_merge:
            print(f"  Loading JSON data from: {json_file}")
            try:
                if os.path.basename(json_file) == os.path.basename(output_json_file):
                    print(f"   Including previously extracted data from {output_json_file} for merge.")
                with open(json_file, 'r', encoding='utf-8') as f:
                    file_data = json.load(f)
                    if isinstance(file_data, list):
                        all_json_data_for_merge.extend(file_data)
                        print(f"    Loaded {len(file_data)} items.")
                    else:
                        print(f"    Warning: File {json_file} does not contain a list at the top level, skipping its data.")
            except Exception as e:
                print(f"   Error loading {json_file}: {e}")

    merge_and_deduplicate_json_data(all_json_data_for_merge, output_json_file)

    print("--- Combined Script Execution Completed ---")
