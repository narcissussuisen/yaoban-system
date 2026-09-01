import sys,pathlib
sys.path.insert(0,r'C:\Users\YZP\WorkBuddy\Claw\方法论与研究文档\yaoban-system\scripts')
import preflight
rc,x=preflight.task_xml('YaobanPreflight')
runner=(preflight.BASE/'scripts'/'run_trading_task.ps1').read_text(encoding='utf-8')
launch=preflight.LAUNCHER.read_text(encoding='utf-8');root=preflight.ROOT_FILE.read_text(encoding='utf-8').strip()
print('rc',rc);print('launcher',str(preflight.LAUNCHER));print('inxml',str(preflight.LAUNCHER) in x);print('runnerok',all(v in runner for v in ('--execute-risk','--e4-support','--temp-ladder','--min-amt','10')));print('launchok','run_trading_task.ps1' in launch and pathlib.Path(root)==preflight.BASE);print('root',root);print(x[x.find('<Actions'):x.find('</Actions>')+10])
