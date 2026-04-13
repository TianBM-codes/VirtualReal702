@echo off
chcp 65001 > nul
echo Starting ODB Service...

start "ODB Web Service" cmd /k "python app.py"
timeout /t 2 /nobreak > nul
start "ODB Job Runner" cmd /k "python src/job_runner.py"

echo Both processes started.
