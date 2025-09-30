# GMN → YouTube Daily Uploader

This repository contains a Python script that automatically compiles the nightly timelapse and summary images from a **Global Meteor Network (GMN) RMS station** and uploads them to YouTube once per day. It adds a background music track and compiles the
timelapse along with 3 second still images of the main rms generated stacked images and graps. 

It was developed for my own GMN station in Ramsbottom, UK, but can be adapted for any RMS installation with the standard folder structure.

---

## ✨ Features

- Scans the **ArchivedFiles** directory of RMS each morning.  
- Chooses the **latest night** (if multiple sessions exist, picks the folder with the longest timelapse).  
- Uploads **only if meteors were detected** (`stack_X_meteors.jpg` with X ≥ 1).  
- Builds a slideshow of RMS summary images and appends it to the timelapse.  
- Adds optional background audio.  
- Uploads to YouTube with a title, description, and tags that include station ID and meteor count.  
- Can be run automatically via `cron`.  

---

## 🛠 Requirements

- **Python 3.7+**
- **ffmpeg + ffprobe**  
  Install on Raspberry Pi / Debian-based systems:  
  ```bash
  sudo apt update && sudo apt install -y ffmpeg
Python libraries (install with pip):
pip install -r requirements.txt
requirements.txt contains:
google-api-python-client>=2.0.0
google-auth>=2.0.0
google-auth-oauthlib>=1.0.0

🔑 YouTube API Setup
Go to Google Cloud Console.
Create a new project.
Enable YouTube Data API v3.
Configure OAuth consent screen:
User type = External
Fill in basic app details (name, support email, etc.)
Important: under Audiance, add a Test user, add the Google account you will use to upload videos (your own Gmail). Without this step, authentication will fail.
Save.
Create OAuth client ID:
Application type = Desktop app
Download the credentials JSON and rename it to:
client_secret.json
Place it at:
/home/rms/youtube/client_secret.json
First run of the script will open a browser to ask you to log in with the Google account you added as a test user.
After you approve, a token will be saved to /home/rms/youtube/token.json so you won’t have to log in again.
Important: if you are using a new youtube account, check you have created a channel and can manually upload a test video.

🚀 Usage
1. Test manually
Run the script once to check everything works:
cd /home/rms/youtube/gmn-youtube-uploader
python3 upload_meteors.py
Set DRY_RUN = True in the script to see what it would upload without actually uploading.
Set DRY_RUN = False to perform a real upload.
2. Add to cron (automatic daily upload)
Edit your crontab:
crontab -e
Add this line to run every day at 10:30 AM:
30 10 * * * /home/rms/vRMS/bin/python /home/rms/youtube/gmn-youtube-uploader/upload_meteors.py >> /home/rms/youtube/cron.log 2>&1
Adjust the Python path if needed (/home/rms/vRMS/bin/python is correct for my venv).
Logs go to /home/rms/youtube/cron.log.

📂 Folder Expectations
The script expects the standard RMS flat archive layout:
/home/rms/RMS_data/ArchivedFiles/
  ├── UK00DF_20250928_182658_xxxxxx/
  ├── UK00DF_20250929_185432_xxxxxx/
  └── ...
Inside each night folder, the script looks for:
images/timelapse.mp4 (or similar)
stack_X_meteors.jpg
RMS report images (fieldsums.png, radiants.png, etc.)

📝 Notes
If multiple RMS folders exist for the same date, the script picks the one with the longest video (if there was an error which can happen).
Only runs an upload if at least one meteor was detected.
Background audio is optional; set the BACKGROUND_AUDIO variable in the script to None if you don’t want it.

Titles and descriptions can be customised in the script’s config block.
📜 License
MIT License — feel free to copy, adapt, and share.

---
