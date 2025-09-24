@echo off
set "PYTHON=C:\Users\sh3qm\PyCharmMiscProject\.venv\Scripts\python.exe"

echo [1/4] Running harvest.py...
python hav_v2.py io33v.lis models\io33v
if errorlevel 1 exit /b %errorlevel%

echo [2/4] Running process.py...
python iv_postproc.py -d models\io33v --component component.yml
if errorlevel 1 exit /b %errorlevel%

echo [3/4] Running reduce_points.py...
python reduce_points.py -d models\io33v --tables iv,vt --method greatest-change --ibis 3.2
if errorlevel 1 exit /b %errorlevel%

echo [4/4] Running render_v3.py...
python render_v3.py
if errorlevel 1 exit /b %errorlevel%

echo All scripts completed successfully!
