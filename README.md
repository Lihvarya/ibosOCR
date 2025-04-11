# 基于i博思的题库提取，实时屏幕 OCR 题目搜索工具（最后更新于2025.4.11）

本项目使您能够对屏幕选定区域（通常包含来自考试、测验或学习资料的题目）执行实时光学字符识别（OCR），然后自动在从 HTML 文件生成的本地数据库中搜索匹配的题目，并在具有模糊搜索功能的浏览器界面中显示结果。


## 目前配适题库（题库数量有限，欢迎各位共同维护）
1.  **网络测试与评价**
2.  **操作系统原理**







**核心组件:**

1.  **`extract_questions.py`**: 从本地 HTML 文件解析题目数据，生成结构化的 JSON 数据库。
2.  **`realtime_ocr.py`**: 捕获屏幕区域，执行 OCR，清理文本，并触发浏览器搜索。
3.  **`search_questions.html`**: 一个 Web 界面，加载 JSON 数据库并使用 Fuse.js 提供模糊搜索功能。

## 功能特性

*   **实时 OCR**: 使用 PaddleOCR 持续监控选定的屏幕区域。
*   **自动搜索**: 当监控区域中的文本发生变化时，在本地题库中触发搜索。
*   **模糊搜索**: 在浏览器中使用 Fuse.js 进行强大的搜索，即使 OCR 结果部分或略有不准确也能匹配。
*   **本地数据**: 完全离线保存您的题库数据。
*   **可配置热键**: 使用键盘快捷键启动/停止 OCR、重新选择捕获区域以及退出应用程序。
*   **HTML 和 JSON 数据整合**:
    *   **HTML 提取**:  从结构化的 HTML 文件中提取题目数据，构建初始题库。
    *   **JSON 合并**:  **新增功能**：自动合并项目目录中所有 JSON 格式的题库数据文件，实现题库数据的统一管理。
    *   **数据去重**:  **新增功能**：在合并过程中，自动检测并移除重复的题目条目，确保题库数据的唯一性和精简性。
*   **区域选择图形界面**: 简单的图形界面，用于定义 OCR 的屏幕区域。
*   **图像预处理**: 基本的图像预处理，可能提高 OCR 准确率。



## 演示视频


https://github.com/user-attachments/assets/0469141c-b061-4482-adcf-d57b468892c9

B站完整版：https://www.bilibili.com/video/BV18WdbYLEFn

## 工作原理

1.  **(一次性设置 / 数据更新):** 运行 `extract_questions.py`。此脚本会查找其目录中的所有 `.html` 文件，解析它们以提取问题、答案和解析，并将所有内容保存到 `combined_questions_data.json`。
2.  **运行工具:** 执行 `realtime_ocr.py`。
    *   此脚本会启动一个本地 Web 服务器，用于提供 `search_questions.html` 和 `combined_questions_data.json` 文件。
    *   如果之前未配置，它会提示您选择一个屏幕区域进行 OCR。
    *   它会监听全局热键。
3.  **激活 OCR:** 按下 `Ctrl+Alt+O` 热键（默认）。
4.  **捕获与搜索:** 将选定的屏幕区域对准您想要查找的题目。
    *   脚本会定期捕获该区域的内容。
    *   它对捕获的图像执行 OCR。
    *   识别出的文本会被清理（去除多余的空格、标点符号等）。
    *   如果清理后的文本是新的且与上次搜索不同，它会在您的默认浏览器中打开 `search_questions.html`，并将清理后的文本作为搜索查询（例如 `http://localhost:8088/search_questions.html?query=你的OCR文本`）。
5.  **查看结果:** `search_questions.html` 页面加载 `combined_questions_data.json`，使用 Fuse.js 根据 URL 查询执行模糊搜索，并显示数据库中最相关的匹配题目，同时高亮显示搜索词。您也可以在该页面的搜索框中手动输入进行搜索。

## 组件说明

### `extract_questions.py`

此脚本现在是项目的 **数据准备和整合工具**。其主要职责已扩展为：

*   **HTML 数据提取 (功能与之前相同):**
    *   **查找源文件:** 使用 `glob` 在其所在目录中搜索所有以 `.html` 扩展名结尾的文件。
    *   **解析 HTML:** 对找到的每个 HTML 文件（`search_questions.html` 本身除外），使用 `BeautifulSoup` 和 `lxml` 解析器分析 HTML 结构。
    *   **识别题目:** 特别查找带有 `question-item` 类的 `<div>` 元素，假定每个这样的 div 代表一个题目块。
    *   **提取数据字段:**  （与之前描述相同，包括 ID, 编号和类型, 题目文本, 选项, 答案, 解析）。
    *   **清理文本:** 使用 `clean_text` 函数去除多余的空白、常见的样板前缀，并处理空值或占位符。
    *   **标记题库名称:** 根据源 HTML 文件名生成可读的“题库名称”。
    *   **整合数据:** 将从所有处理过的 HTML 文件中提取的所有题目字典聚合到一个列表中。
    *   **初步 JSON 输出:**  将提取的题目数据 **立即保存到** `combined_questions_data.json` 文件中，作为中间步骤和初始数据备份。

*   **JSON 数据合并与去重 (新增功能):**
    *   **查找 JSON 文件:** 在脚本所在目录中，使用 `glob` 查找所有以 `*.json` 结尾的文件，**包括刚刚从 HTML 提取生成的 `combined_questions_data.json` 以及任何其他 JSON 文件**。
    *   **加载 JSON 数据:** 从找到的每个 JSON 文件中加载数据。脚本 **假设每个 JSON 文件都包含一个题目列表** 作为顶层结构。如果文件内容不是列表，则会发出警告并跳过该文件的数据。
    *   **合并数据:** 将从所有 JSON 文件加载的数据合并到一个总列表中。
    *   **数据去重:**  使用基于 JSON 序列化签名的方法，检测并移除 **完全重复** 的题目条目。去重过程会记录移除的重复条目数量，并保留第一个遇到的唯一条目。
    *   **最终 JSON 输出:** 将去重后的 **唯一题目数据** 列表 **覆盖保存回** `combined_questions_data.json` 文件。这意味着最终的 `combined_questions_data.json` 文件将包含从所有 HTML 文件提取并与所有其他 JSON 文件合并，并且去重后的结果。

**关键更新说明:**

*   `extract_questions.py` 现在不仅处理 HTML 文件，还会 **合并当前目录下所有 `*.json` 文件的数据**，并对所有数据进行 **去重**。
*   脚本执行流程变为：先从 HTML 提取并保存初始 JSON，然后加载所有 JSON 文件进行合并和去重，最后将去重结果覆盖保存到 `combined_questions_data.json`。
*   合并过程包括之前生成的 `combined_questions_data.json` 本身，这意味着您可以多次运行 `extract_questions.py`，它会将新的 HTML 提取结果与之前的数据合并，并自动去重，保持 `combined_questions_data.json` 内容的最新和唯一。

**注意:**  此脚本仍然期望源 HTML 文件具有一致的结构以便提取题目信息。同时，合并的 JSON 文件应包含题目列表。如有需要，您可能需要根据您的 HTML 和 JSON 数据结构调整脚本中的选择器和数据处理逻辑。



### `realtime_ocr.py`

主应用程序脚本。它协调屏幕捕获、OCR 过程、热键监听以及与搜索界面的通信。它依赖于：

*   `mss`: 用于高效的屏幕捕获。
*   `PaddleOCR`: 用于核心的光学字符识别。
*   `opencv-python`: 用于图像预处理（灰度转换、阈值处理）。
*   `keyboard`: 用于捕获全局热键（需要管理员/root 权限）。
*   `http.server` / `socketserver`: 用于运行简单的本地 Web 服务器。
*   `webbrowser`: 用于打开搜索结果页面。

### `search_questions.html`

一个自包含的 HTML 文件，用作搜索前端界面。

*   使用标准的 HTML、CSS 和 JavaScript。
*   异步获取 `combined_questions_data.json`。
*   集成 `Fuse.js`（从 CDN 加载）以在加载的题目数据上执行客户端模糊搜索。
*   动态渲染搜索结果，高亮显示匹配的术语。
*   响应由 `realtime_ocr.py` 传递的 URL 查询参数（`?query=...`）。
*   允许通过输入字段进行手动搜索。

## 安装

1.  **克隆仓库:**
    ```bash
    git clone <你的仓库URL>
    cd <仓库目录>
    ```

2.  **先决条件:**
    *   Python 3.7+
    *   `pip` (Python 包安装器)
    *   **PaddlePaddle:** 根据您的系统安装 CPU 或 GPU 版本。请遵循官方指南：[PaddlePaddle 安装指南](https://www.paddlepaddle.org.cn/install/quick?docurl=/documentation/docs/zh/install/pip/linux-pip_cn.html)
    *   **(Windows 特定):** 您可能需要 Microsoft Visual C++ Redistributable。PaddleOCR 可能需要特定的依赖项；请查阅其文档。
    *   **(Linux 特定):** `keyboard` 库可能需要 root 权限才能捕获全局热键。`mss` 可能需要特定的 X11 库。

3.  **安装 Python 依赖:**
    创建一个名为 `requirements.txt` 的文件，包含以下内容：

    ```txt
    paddleocr>=2.6 # 或者你测试时使用的特定版本
    # paddlepaddle 或 paddlepaddle-gpu (根据官方指南手动安装)
    opencv-python
    numpy
    beautifulsoup4
    lxml
    mss
    keyboard
    ```

    然后安装它们：
    ```bash
    pip install -r requirements.txt
    ```
    *请记住单独安装正确的 `paddlepaddle` 版本。*

4.  **准备题目数据:**
    *   将您的源 HTML 题库文件（例如 `exam1.html`, `quiz_topicA.html`）放在与 Python 脚本相同的目录中。
    *   运行提取脚本：
        ```bash
        python extract_questions.py
        ```
    *   验证 `combined_questions_data.json` 文件已成功创建。

## 使用方法

1.  **运行 OCR 工具:**
    以 **管理员（Windows）** 或使用 **`sudo`（Linux/macOS）** 打开终端或命令提示符，因为 `keyboard` 库需要提升权限才能监听全局热键。
    ```bash
    sudo python realtime_ocr.py
    # 或者在 Windows (管理员终端) 中:
    python realtime_ocr.py
    ```

2.  **选择区域 (首次 / 重新选择):**
    *   如果没有 `ocr_config.json` 文件，或者您按下了重新选择的热键（默认为 `Ctrl+Alt+R`），屏幕上会出现一个半透明的覆盖层。
    *   单击并拖动以在您想要监控题目的屏幕区域周围绘制一个矩形。
    *   释放鼠标按钮以确认。坐标将保存到 `ocr_config.json`。

3.  **使用热键:**
    *   **`Ctrl+Alt+O` (默认):** 切换连续 OCR 的开启/关闭。开启时，脚本会监控区域并执行搜索。
    *   **`Ctrl+Alt+R` (默认):** 停止 OCR（如果活动），并重新运行区域选择过程。
    *   **`Ctrl+Alt+Q` (默认):** 优雅地退出 `realtime_ocr.py` 脚本，并关闭服务器。

4.  **执行搜索:**
    *   确保 OCR 已激活（如果需要，请按 `Ctrl+Alt+O`）。
    *   将定义的屏幕区域定位在您想要搜索的题目文本上。
    *   稍等片刻（由脚本中的 `OCR_INTERVAL_SECONDS` 控制）。
    *   如果 OCR 检测到新文本，您的默认浏览器应打开/聚焦一个带有 `search_questions.html` 的标签页，显示检测到的文本的搜索结果。

## 配置

您可以直接在 `realtime_ocr.py` 脚本中调整设置：

*   `OCR_LANG`: PaddleOCR 的语言模型（例如，'ch' 用于中文+英文，'en' 用于英文）。
*   `USE_GPU`: 如果您有兼容的 GPU 并安装了 GPU 版本的 PaddlePaddle，则设置为 `True`。
*   `CONFIG_FILE`: 存储所选区域坐标的文件名。
*   `TOGGLE_OCR_HOTKEY`, `RESELECT_HOTKEY`, `QUIT_HOTKEY`: 更改键盘快捷键。
*   `SERVER_PORT`: 本地 Web 服务器的端口号（如果 8088 被占用，请更改）。
*   `OCR_INTERVAL_SECONDS`: 当 OCR 激活时，脚本捕获和处理屏幕区域的频率（以秒为单位）。

在 `search_questions.html` 中，您可以配置 `fuseOptions` JavaScript 对象来微调模糊搜索行为（例如 `threshold`, `keys`, `weight`）。

## 故障排除

*   **热键不工作:** 确保您是以管理员/root 权限运行 `realtime_ocr.py`。检查是否与其他使用相同热键的应用程序冲突。
*   **服务器错误 / 端口冲突:** 如果脚本报告启动 Web 服务器时出错，则 `SERVER_PORT`（默认为 8088）可能已被其他应用程序占用。请在 `realtime_ocr.py` 中更改端口号。
*   **OCR 不准确:**
    *   确保所选区域紧密贴合文本区域。
    *   如果可能，尝试调整光线或背景对比度。
    *   尝试调整 `realtime_ocr.py` 中的 `preprocess_screen_capture` 函数（例如，调整 `adaptiveThreshold` 的 `blockSize` 和 `C` 参数）。
    *   确保设置了正确的 `OCR_LANG`。
*   **"无法加载题目数据" / 搜索页空白:**
    *   确保您已成功运行 `python extract_questions.py` 并且 `combined_questions_data.json` 文件存在于同一目录中。
    *   检查 `combined_questions_data.json` 是否为空且包含有效的 JSON 数据。
    *   确保 `realtime_ocr.py` 正在运行（它负责提供文件）。
    *   检查浏览器开发者控制台（F12）在加载 `search_questions.html` 时是否有错误。
*   **提取失败 / `combined_questions_data.json` 为空:**
    *   验证您的源 HTML 文件是否包含预期的结构（`div.question-item` 等）。您可能需要调整 `extract_questions.py` 中的选择器。
    *   检查运行 `extract_questions.py` 时的控制台输出是否有错误。

## 依赖项

*   Python 3.x
*   PaddleOCR & PaddlePaddle (CPU or GPU)
*   OpenCV (`opencv-python`)
*   NumPy
*   Beautiful Soup 4 (`beautifulsoup4`)
*   lxml
*   MSS (`mss`)
*   Keyboard (`keyboard`)
*   Fuse.js (通过 CDN 包含在 `search_questions.html` 中)

## 许可证

本项目根据 Apache-2.0 许可证授权 

## 致谢

*   [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) 开发团队。
*   [Fuse.js](https://fusejs.io/) 提供客户端模糊搜索功能。
*   相关库: MSS, Keyboard, BeautifulSoup, OpenCV, NumPy。




