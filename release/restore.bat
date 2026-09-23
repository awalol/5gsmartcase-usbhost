@echo off
chcp 936 >nul
cd /d "%~dp0"

rem ============================================================
rem  Add tools\ to PATH so adb / spd_dump resolve from anywhere
rem ============================================================
set "PATH=%~dp0tools;%PATH%"

rem 上传到设备的目标路径
set "ROUTE_DST=/etc/route_test.sh"

rem 等待设备上线的超时时间（秒）
set "WAIT_MAX=300"

cls
echo 华为5G手机恢复原厂系统 v4.0
echo.
echo 请选择设备型号：
echo   1. mate50rs
echo   2. p60
echo.
set "DEV="
set /p model=请输入序号并回车:

if "%model%"=="1" set "DEV=mate50rs"
if "%model%"=="2" set "DEV=p60"

if not defined DEV (
    echo.
    echo 无效的型号选择，正在退出...
    timeout /t 2 >nul
    exit /b
)

rem ============================================================
rem  刷机前先确认镜像文件齐全，避免刷到一半失败
rem ============================================================
if not exist "restore\%DEV%\uboot.bin" (
    echo.
    echo [错误] 缺少文件：restore\%DEV%\uboot.bin
    pause
    exit /b
)
if not exist "restore\%DEV%\boot.bin" (
    echo.
    echo [错误] 缺少文件：restore\%DEV%\boot.bin
    pause
    exit /b
)
if not exist "restore\route_test.sh" (
    echo.
    echo [错误] 缺少文件：restore\route_test.sh
    pause
    exit /b
)

cls
echo 当前型号：%DEV%
echo.
echo 注意：恢复过程中请勿断开数据线、勿断电。
set /p choice=是否确认开始恢复？(Y/N):

if /i not "%choice%"=="Y" (
    echo.
    echo 已取消恢复，正在退出...
    timeout /t 2 >nul
    exit /b
)

echo.
echo =============================================
echo  第一步：刷入 uboot 与 boot
echo =============================================
echo. ---------------------------------------------
echo  1.短接刷机点，然后立即将数据线连接到电脑
echo  2.若已识别到端口后，断开短接点，开始刷入
echo. ---------------------------------------------
echo.
echo 请现在短接并进入U2S刷机模式，程序将等待600秒...
echo.
spd_dump --wait 600 skip_confirm 1 exec_addr 0x3f28 fdl tools\fdl1 0x28007000 fdl tools\fdl2 0x9efffe00 exec timeout 180000 w uboot restore\%DEV%\uboot.bin w boot restore\%DEV%\boot.bin reset

echo.
echo =============================================
echo  第二步：等待设备重新上线
echo =============================================
adb kill-server >nul 2>&1
adb start-server >nul 2>&1
echo 正在等待设备进入系统，最长等待 %WAIT_MAX% 秒...
set /a WAIT=0

:waitdev
set "DEVL="
for /f "skip=1 tokens=1,2" %%A in ('adb devices 2^>nul') do (
    if /i "%%B"=="device" set "DEVL=%%A"
)
if not defined DEVL goto waitmore

rem 设备已枚举，再确认 shell 可用
adb shell echo ok >nul 2>&1
if errorlevel 1 goto waitmore

goto devready

:waitmore
set /a WAIT+=2
if %WAIT% GEQ %WAIT_MAX% (
    echo.
    echo [错误] 等待设备上线超时，设备未进入系统。
    echo 请检查数据线连接，或重新插拔数据线后重试。
    pause
    exit /b
)
timeout /t 2 >nul
goto waitdev

:devready
echo 已检测到设备：%DEVL%

echo.
echo =============================================
echo  第三步：上传 route_test.sh
echo =============================================
echo 正在将根目录挂载为可写...
adb shell mount -o remount,rw /
if errorlevel 1 (
    echo.
    echo [错误] 重新挂载根目录失败。
    pause
    exit /b
)

echo 正在上传 %ROUTE_DST% ...
adb push restore\route_test.sh %ROUTE_DST%
if errorlevel 1 (
    echo.
    echo [错误] 上传 %ROUTE_DST% 失败。
    pause
    exit /b
)

adb shell chmod 755 %ROUTE_DST%
if errorlevel 1 (
    echo.
    echo [错误] 设置 %ROUTE_DST% 权限失败。
    pause
    exit /b
)
echo 上传完成。
adb shell ls -l %ROUTE_DST%

echo.
echo =============================================
echo  第四步：重启设备
echo =============================================
adb reboot
if errorlevel 1 (
    echo.
    echo [错误] 重启命令下发失败，请手动重启设备。
    pause
    exit /b
)

echo.
echo =============================================
echo  恢复流程结束
echo =============================================
echo 设备正在重启，请等待其重新进入系统。
echo.
echo 异常处理：
echo 1.若中途断开数据线，可重新短接进入刷机模式再试。
echo 2.若出现任何异常、设备无法开机，请将日志和截图发到群里。
pause
