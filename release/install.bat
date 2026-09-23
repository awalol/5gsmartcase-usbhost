@echo off
chcp 936 >nul
cd /d "%~dp0"

rem ============================================================
rem  Add tools\ to PATH so adb / spd_dump resolve from anywhere
rem ============================================================
set "PATH=%~dp0tools;%PATH%"

rem ============================================================
rem  各型号分区校验值（SHA256）
rem ============================================================
set "MATE50RS_MTD1RO_SHA256=a8e29e155eaa16430b9e368d92a010031d0f730ab60ddb818b7056878c57c54c"
set "MATE50RS_BOOT_SHA256=ad03828d8a69e936ddd97b25a05eac64e0624289a527ed2e9271d5a7416eeb6c"
set "P60_MTD1RO_SHA256=6dad2c6670a4055143c2a1e628f136580d1265947166f903408a26b9221306e3"
set "P60_BOOT_SHA256=e0db8ab6fa30cdd653a33dbf2f505f98300273c055b7a31b0efe0607ebd4b198"

rem 参与校验的分区节点
set "MTD1RO_DEV=/dev/mtd1ro"
set "BOOT_DEV=/dev/ubi0_boot"

cls
echo 华为5G手机安装测试系统 v4.0
echo.
echo 请选择设备型号：
echo   1. mate50rs
echo   2. p60
echo.
set "DEV="
set "EXP_MTD1RO="
set "EXP_BOOT="
set /p model=请输入序号并回车:

if "%model%"=="1" (
    set "DEV=mate50rs"
    set "EXP_MTD1RO=%MATE50RS_MTD1RO_SHA256%"
    set "EXP_BOOT=%MATE50RS_BOOT_SHA256%"
)
if "%model%"=="2" (
    set "DEV=p60"
    set "EXP_MTD1RO=%P60_MTD1RO_SHA256%"
    set "EXP_BOOT=%P60_BOOT_SHA256%"
)

if not defined DEV (
    echo.
    echo 无效的型号选择，正在退出...
    timeout /t 2 >nul
    exit /b
)

cls
echo 当前型号：%DEV%
echo.
echo 注意：刷机有风险，请自行承担刷机失败、设备损坏的后果。
set /p choice=是否同意自行承担风险，开始安装？(Y/N):

if /i not "%choice%"=="Y" (
    echo.
    echo 取消安装，正在退出...
    timeout /t 2 >nul
    exit /b
)

echo.
echo =============================================
echo  第一步：确认手机已正常进入系统
echo =============================================
echo 请确保手机已正常开机进入系统，并且已用数据线连接电脑、已开启USB调试。
echo.
adb devices
adb shell echo ok >nul 2>&1
if errorlevel 1 (
    echo.
    echo [错误] 未检测到设备，请检查数据线、USB调试是否已打开。
    pause
    exit /b
)
echo.
echo 已检测到设备，按任意键继续...
pause >nul

echo.
echo =============================================
echo  第二步：校验设备分区
echo =============================================
echo 正在读取 %MTD1RO_DEV% 的校验值...
set "H_MTD1RO="
for /f "tokens=1" %%H in ('adb shell sha256sum %MTD1RO_DEV% 2^>nul') do set "H_MTD1RO=%%H"
set "H_MTD1RO=%H_MTD1RO:~0,64%"

echo 正在读取 %BOOT_DEV% 的校验值...
set "H_BOOT="
for /f "tokens=1" %%H in ('adb shell sha256sum %BOOT_DEV% 2^>nul') do set "H_BOOT=%%H"
set "H_BOOT=%H_BOOT:~0,64%"

echo.
echo   %MTD1RO_DEV%
echo     实际：%H_MTD1RO%
echo     期望：%EXP_MTD1RO%
echo   %BOOT_DEV%
echo     实际：%H_BOOT%
echo     期望：%EXP_BOOT%
echo.

if /i not "%H_MTD1RO%"=="%EXP_MTD1RO%" (
    echo [错误] %MTD1RO_DEV% 校验值不匹配，设备型号或固件版本不正确，已中止安装。
    pause
    exit /b
)
if /i not "%H_BOOT%"=="%EXP_BOOT%" (
    echo [错误] %BOOT_DEV% 校验值不匹配，设备型号或固件版本不正确，已中止安装。
    pause
    exit /b
)
echo 校验通过，设备与型号 %DEV% 匹配。

echo.
echo =============================================
echo  第三步：重新挂载根目录为读写
echo =============================================
adb shell mount -o remount,rw /
if errorlevel 1 (
    echo.
    echo [错误] 重新挂载根目录失败。
    pause
    exit /b
)
echo 挂载完成。

echo.
echo =============================================
echo  第四步：上传 route_test.sh
echo =============================================
adb push upload\route_test.sh /etc/route_test.sh
if errorlevel 1 (
    echo.
    echo [错误] 上传 /etc/route_test.sh 失败。
    pause
    exit /b
)

echo.
echo =============================================
echo  第五步：上传 root 目录内容到 /home/root
echo =============================================
adb shell mkdir -p /home/root
for /d %%D in (upload\root\*) do (
    if exist "%%D\" adb push "%%D" /home/root/
)
for %%F in (upload\root\*) do (
    if not exist "%%F\" adb push "%%F" /home/root/
)
adb shell chmod -R 755 /home/root
adb shell chmod 755 /etc/route_test.sh
echo.
echo 上传完成，/home/root 内容如下：
adb shell ls -l /home/root

echo.
echo =============================================
echo  第六步：进入U2S刷机模式
echo =============================================
echo. ---------------------------------------------
echo  1.短接刷机触点，然后将数据线连接到电脑
echo  2.电脑识别到端口后，断开短接触点，开始刷机
echo. ---------------------------------------------
echo.
echo 请现在短接并进入U2S刷机模式，程序将等待600秒...
echo.
spd_dump --wait 600 skip_confirm 1 exec_addr 0x3f28 fdl tools\fdl1 0x28007000 fdl tools\fdl2 0x9efffe00 exec timeout 180000 w uboot %DEV%\uboot.bin w boot %DEV%\boot.bin reset

echo.
echo.
echo =============================================
echo  安装流程结束
echo =============================================
echo 若刷机全部成功，会看到2个分区均已写入的提示。
echo 若未出现 send_fail 等错误提示，说明安装成功。
echo 随后等待设备重启，然后访问 192.168.66.1（不需要加端口号）。
echo.
echo 异常处理：
echo 1.如果中途断开，请重新短接进入刷机模式后重试。
echo 2.如果出现任何异常或设备无法开机，请将日志和截图发到群里。
pause
