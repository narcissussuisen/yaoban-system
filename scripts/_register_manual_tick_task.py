# -*- coding: utf-8 -*-
"""注册一次性计划任务 YaobanManualTick20260902（触发 manual_tick_unblock 启动器）。

复刻 yaoban 现有任务的可用模式：pythonw.exe + run_hidden.py + InteractiveToken + 本机 SID。
XML 以 UTF-16 LE BOM 写出（schtasks 导入要求），规避中文路径的 shell 引号问题。
"""
import pathlib
import subprocess

XML_PATH = pathlib.Path(r"C:\Users\YZP\WorkBuddy\yaoban_tasks\manual_tick_20260902_task.xml")
TASK_NAME = "YaobanManualTick20260902"

xml = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>2026-09-02 one-shot: unblock yaoban sell executor (manual intervention, see task_logs/2026-09-02/20260902_manual_tick.json)</Description>
  </RegistrationInfo>
  <Principals>
    <Principal id="Author">
      <UserId>S-1-5-21-1266151948-784291992-2083257385-1001</UserId>
      <LogonType>InteractiveToken</LogonType>
    </Principal>
  </Principals>
  <Settings>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
    <Hidden>true</Hidden>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <StartWhenAvailable>true</StartWhenAvailable>
    <WakeToRun>false</WakeToRun>
  </Settings>
  <Triggers>
    <TimeTrigger>
      <StartBoundary>2026-09-02T09:25:00+08:00</StartBoundary>
    </TimeTrigger>
  </Triggers>
  <Actions Context="Author">
    <Exec>
      <Command>C:\\Users\\YZP\\.workbuddy\\binaries\\python\\versions\\3.13.12\\pythonw.exe</Command>
      <Arguments>"C:\\Users\\YZP\\WorkBuddy\\yaoban_tasks\\run_hidden.py" "C:\\Users\\YZP\\.workbuddy\\binaries\\python\\envs\\default\\Scripts\\python.exe" -X utf8 "C:\\Users\\YZP\\WorkBuddy\\Claw\\方法论与研究文档\\EvoAlpha\\yaoban-system\\scripts\\manual_tick_unblock_20260902.py"</Arguments>
    </Exec>
  </Actions>
</Task>
"""

XML_PATH.write_bytes(xml.encode("utf-16"))
print("xml written:", XML_PATH)
r = subprocess.run(["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(XML_PATH),
                    "/F"], capture_output=True)
print("rc:", r.returncode)
print("out:", (r.stdout or b"").decode("gbk", errors="replace").strip())
print("err:", (r.stderr or b"").decode("gbk", errors="replace").strip())
