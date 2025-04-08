import json
import re
import os
import glob # To find files matching a pattern
from bs4 import BeautifulSoup

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
    # Remove extension and replace common separators with spaces
    name_part = os.path.splitext(base_name)[0]
    # Optionally replace underscores/hyphens with spaces for readability
    # name_part = name_part.replace('_', ' ').replace('-', ' ')
    return name_part if name_part else "Unknown Bank"

def extract_questions_from_html(html_filepath, bank_name):
    """
    Extracts question data from a single HTML file and tags it with the bank name.

    Args:
        html_filepath (str): Path to the HTML file.
        bank_name (str): The name of the question bank derived from the filename.

    Returns:
        list: A list of dictionaries for questions found in this file.
    """
    extracted_data = []
    print(f"--- Processing: {html_filepath} (Bank: {bank_name}) ---")
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

    print(f"   Found {len(question_items)} potential question items.")

    for item in question_items:
        question_data = {'bank_name': bank_name} # Add bank name tag
        try:
            # --- Extract Question ID ---
            question_data['id'] = item.get('id', f'unknown_{bank_name}_{len(extracted_data)}')

            # --- Extract Number and Type ---
            type_tag = item.find('p', class_='question-item__type')
            q_num = None
            q_type = "未知类型"
            if type_tag:
                type_text = clean_text(type_tag.get_text(strip=True))
                match = re.match(r'(\d+)\.【(\S+)】', type_text)
                if match:
                    q_num = int(match.group(1))
                    q_type = match.group(2)
                else:
                    # Try to extract number if pattern fails but text starts with number.
                    num_match = re.match(r'(\d+)\.', type_text)
                    if num_match:
                        q_num = int(num_match.group(1))
                    q_type = type_text # Keep raw type if pattern fails
            question_data['number'] = q_num
            question_data['type'] = q_type


            # --- Extract Question Content ---
            content_tag = item.find('div', class_='question-item__content')
            question_data['text'] = clean_text(content_tag.get_text(separator='\n', strip=True)) if content_tag else "内容未找到"

            # --- Extract Options ---
            options_list = []
            options_ul = item.find('ul', class_='question-item__option')
            if options_ul:
                options_li = options_ul.find_all('li')
                for li in options_li:
                     option_full_text = clean_text(li.get_text(strip=True))
                     options_list.append(option_full_text)
            question_data['options'] = options_list

            # --- Extract Answers ---
            answer_div = item.find('div', class_='stu-answer')
            student_answer = "未提供"
            correct_answer = "未提供"
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

            # --- Extract Analysis ---
            analysis_tag = item.find('div', class_='analysis')
            analysis_text = ""
            if analysis_tag:
                analysis_content = analysis_tag.find('div', class_='analysis-content')
                if analysis_content:
                     analysis_text = clean_text(analysis_content.get_text(separator='\n', strip=True))
                else:
                     base_text_tag = analysis_tag.find('p') # Try finding any <p> inside analysis
                     if base_text_tag and "本题解析" in base_text_tag.get_text():
                        # Find the span inside the p for the actual text
                        analysis_span = base_text_tag.find('span')
                        if analysis_span:
                            analysis_text = clean_text(analysis_span.get_text(strip=True))
                        else: # Fallback if span isn't there
                            analysis_text = clean_text(base_text_tag.get_text(strip=True))
                            analysis_text = re.sub(r'^本题解析\s*：【无】', '', analysis_text).strip() # Clean specific common pattern

                     else: # General fallback if structure is unexpected
                          analysis_text = clean_text(analysis_tag.get_text(separator='\n', strip=True))
                          analysis_text = re.sub(r'本题解析.*$', '', analysis_text).strip() # Remove button text

            question_data['analysis'] = analysis_text if analysis_text else "无"

            extracted_data.append(question_data)

        except Exception as e:
            print(f"   Error processing a question item in {bank_name} (ID: {item.get('id', 'N/A')}): {e}")
            # Optionally add a placeholder for the failed item
            # extracted_data.append({"id": item.get('id', 'N/A'), "error": str(e), "bank_name": bank_name})

    print(f"   Successfully extracted {len(extracted_data)} questions from this file.")
    return extracted_data

# --- Main Execution ---
if __name__ == "__main__":
    # Directory containing the HTML question bank files
    # Use '.' for the current directory, or specify a path like 'C:/Users/YourUser/Documents/QuizFiles'
    html_source_directory = '.'
    output_json_file = 'combined_questions_data.json'

    all_extracted_questions = []

    # Find all .html files in the specified directory
    html_files = glob.glob(os.path.join(html_source_directory, '*.html'))

    if not html_files:
        print(f"No .html files found in directory: {os.path.abspath(html_source_directory)}")
    else:
        print(f"Found {len(html_files)} HTML files to process...")
        for html_file in html_files:
             # Skip the search page itself if it's in the same directory
             if os.path.basename(html_file) == 'search_questions.html':
                 print(f"--- Skipping search page: {html_file} ---")
                 continue

             bank_name = get_bank_name_from_filepath(html_file)
             questions_from_file = extract_questions_from_html(html_file, bank_name)
             if questions_from_file:
                 all_extracted_questions.extend(questions_from_file) # Add questions to the main list

        print("-" * 20)
        if all_extracted_questions:
            # Optional: Sort the combined list if needed (e.g., by bank then number)
            # all_extracted_questions.sort(key=lambda x: (x.get('bank_name', ''), x.get('number') if x.get('number') is not None else float('inf')))

            try:
                with open(output_json_file, 'w', encoding='utf-8') as f:
                    json.dump(all_extracted_questions, f, indent=4, ensure_ascii=False)
                print(f"Successfully combined and saved {len(all_extracted_questions)} questions from {len(html_files)} file(s) to {output_json_file}")
            except Exception as e:
                print(f"Error writing combined JSON file {output_json_file}: {e}")
        else:
            print("No questions were extracted from any file.")