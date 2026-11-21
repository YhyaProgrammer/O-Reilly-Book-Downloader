# 📚 O'Reilly Book Downloader

**A Python-based book search, download, and conversion tool built with a modular microkernel architecture.**

Search for books on O'Reilly, retrieve book content using an authenticated session, and convert supported content into multiple formats for personal learning, research, and educational workflows.

<p align="center">

  🐍 Python 3.10+ · 🧩 Microkernel Architecture · 📖 Multiple Export Formats

</p>

---

## ✨ Features

- 🔎 **Book Search** – Search the O'Reilly catalog using book titles, ISBNs, and supported identifiers.
- 🍪 **Cookie-Based Authentication** – Use your own authorized O'Reilly browser session cookies.
- 📥 **Book Downloading** – Retrieve available book chapters and associated resources.
- 🌍 **Multi-Language Support** – Search for translated editions, including Japanese and English.
- 📘 **EPUB Export** – Generate structured EPUB books.
- 📝 **Markdown Export** – Convert book content into Markdown.
- 📄 **PDF Export** – Generate PDF documents.
- 📃 **Plain Text Export** – Extract readable text.
- 🔧 **JSON Export** – Export structured book content and metadata.
- 🖥️ **Web Interface** – Search and manage downloads through a browser-based interface.
- 🧩 **Plugin Architecture** – Extend functionality using independent Python plugins.

## 🖥️ Interface

The project includes a lightweight web interface for interacting with the downloader.

**Supported operations include:**

- Searching for books.
- Viewing book information.
- Starting downloads.
- Monitoring download progress.

---

## 🛠️ Technology Stack

| Technology | Purpose |
|---|---|
| Python 3.10+ | Main programming language |
| Vanilla Python | Core application |
| HTTP Client | HTTP requests and content retrieval |
| HTML Processing | Extracting and processing chapter content |
| EPUB / ZIP | EPUB generation |
| `http.server` | Built-in web server |
| JavaScript | Web interface |
| CSS | Interface styling |

---

## ⚙️ Installation

### Requirements

- Python 3.10 or newer
- Git
- An authorized O'Reilly account and session, where required

### 1. Clone the repository

```bash
git clone https://github.com/YhyaProgrammer/oreilly-downloader.git
cd oreilly-downloader
```

### 2. Create a virtual environment

**Windows:**

```powershell
python -m venv .venv
.venv\Scripts\activate
```

**Linux / macOS:**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

---

## 🚀 Running the Application

Start the web server:

```bash
python main.py
```

By default, the application runs on:

```text
http://localhost:8000
```

To use a custom port:

```bash
python main.py --port 9000
```

---

## 🔄 How It Works

The application follows a modular content-processing pipeline:

```text
       🔎 Book Search
             |
             ▼
      🔐 Authentication
             |
             ▼
      📚 Book Metadata
             |
             ▼
      📖 Chapter Retrieval
             |
             ▼
      🖼️ Assets Processing
             |
             ▼
      🧹 HTML Processing
             |
             ▼
       📦 Output Generation
             |
      ┌──────┼───────┐
      ▼      ▼       ▼
    EPUB  Markdown   PDF
      |
      ▼
   JSON / TXT
```

### Content Pipeline

1. Retrieve book metadata.
2. Fetch the available chapter list.
3. Retrieve chapter content.
4. Process HTML and associated assets.
5. Rewrite internal links and resource paths.
6. Generate the selected output format.

---

## 🔐 Authentication & Privacy

This project uses user-provided session cookies for authorized access.

- 🍪 Session cookies should belong to your own account.
- 🔒 Never commit `cookies.json` or session credentials to GitHub.
- 🚫 Never share authentication tokens publicly.
- 🛡️ Keep credentials in local configuration files excluded through `.gitignore`.

Authentication and access must follow the applicable O'Reilly terms and account permissions.

---

## ⚠️ Educational Purpose & Disclaimer

This project is developed for **educational purposes, Python programming practice, software architecture experimentation, and personal learning workflows**.

- Users are responsible for ensuring they have permission to access and process the content they retrieve.
- Only download content that you are authorized to access and use.
- Respect copyright laws, licensing restrictions, and O'Reilly's Terms of Service.
- This project is not affiliated with, endorsed by, or officially associated with O'Reilly Media.
- Do not redistribute copyrighted books without permission.

---

## 🤝 Contributing

Contributions, bug reports, and suggestions are welcome!

1. Fork the repository.
2. Create a feature branch.
3. Make your changes.
4. Test your implementation.
5. Submit a Pull Request.

---

## 👨‍💻 Author

**YhyaProgrammer**

GitHub: https://github.com/YhyaProgrammer

---

<p align="center">

  🐍 Built with Python · 📚 Made for Learning · 🚀 Open to Contributions

</p>
