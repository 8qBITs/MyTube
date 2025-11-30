# MyTube

**MyTube** is a fully self-hosted, private YouTube-style video platform built with **Flask + SQLAlchemy + Jinja + Bootstrap 5**.  
It can host **any kind of video library** — movies, personal clips, training videos, NSFW content — anything your server can store.  
MyTube is designed for **private audiences**, **self-hosting**, and **full admin control**.

You can disable registration, restrict access, ban users, customize branding, and manage everything from a clean admin panel.

---

## ✨ Features

### 🎥 **Video hosting & streaming**
- Upload any video format your browser supports (MP4/MKV recommended).
- Real HTTP **range-based streaming** (fast seeking, efficient playback).
- Auto-generated **thumbnails** via FFmpeg.
- Video metadata stored in database; video files stored on disk.
- View counter that increments only on real watches.

### 🧭 **Front-end interface**
- Home, Watch History, and Liked Videos tabs.
- Search (title + description).
- Sort: newest, oldest, A–Z, Z–A.
- Pagination (6 videos/page).
- Responsive video grid (1–3 columns).
- “Watched” badge based on history.
- Dark modern theme with wider layout.

### 🙍‍♂️ **User system**
- Email/password authentication.
- Admin flag and ban flag.
- Banned users cannot watch or interact.
- Optional profile fields: username, gender, about, privacy toggles.
- **Registration can be disabled** entirely (private site mode).

### ❤️ **Likes, history & recommendations**
- WatchHistory tracks every user’s last-watched time.
- Like/Dislike toggle system like YouTube.
- Liked page shows all liked videos.
- Related videos on each watch page.

### 💬 **Comments**
- Nested replies (one level).
- Anonymous comments supported.
- Comment likes.
- Admin-highlighted (“hearted”) comments.

### 🧑‍💼 **Admin panel**
- Dashboard with system stats.
- Fully configurable:
  - **Registration enabled/disabled**
  - **Site name**
  - **Footer text**
  - **DeepSeek API key**
- Manage users: create, ban/unban, promote to admin.
- Manage videos:
  - Edit title/description.
  - Delete video + files.
  - Regenerate thumbnails.
  - Search & filter list.
  - Upload single or in bulk.
  - Auto-discover files on disk not yet in database.

### 🤖 **AI integration (DeepSeek)**
- Generate missing **title and description** automatically.
- Per-video “AI metadata” button.
- Bulk mode:
  - Finds videos with missing/placeholder metadata.
  - Calls AI for each.
  - Shows fullscreen progress overlay (cancel supported).

### ⚙️ **Configurable & private**
Everything can be enabled, disabled, or branded:
- Registration toggle
- Branding text
- Footer text
- AI integration
- Admin-only access if desired  
MyTube works great as a **fully private personal video vault**.

---

## 🛠️ Tech Stack

- **Backend:** Flask (Python)
- **Database:** SQLAlchemy (SQLite by default)
- **Templates:** Jinja2 + Bootstrap 5
- **Styling:** Bootstrap Icons + custom CSS
- **Video processing:** FFmpeg (thumbnail generation)
- **AI:** DeepSeek Chat API

---

## 📦 Installation

### 1. Install dependencies
```bash
pip install -r requirements.txt
