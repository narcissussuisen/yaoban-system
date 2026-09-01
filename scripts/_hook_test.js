
const { execSync } = require('child_process');
const input = "{\"cwd\":\"C:\\\\Users\\\\YZP\\\\WorkBuddy\\\\Claw\\\\方法论与研究文档\\\\Vibe-Research\\\\.local\\\\runs\\\\20260829-175556-601212\",\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"Get-Content C:\\\\Users\\\\YZP\\\\WorkBuddy\\\\Claw\\\\方法论与研究文档\\\\Vibe-Research\\\\.local\\\\runs\\\\20260829-175556-601212\\\\.vibe\\\\hook-context.json\"},\"hook_event_name\":\"PreToolUse\"}";
try {
  const r = execSync('node "C:/Users/YZP/WorkBuddy/Claw/方法论与研究文档/Vibe-Research/orchestrator/hooks/pre_tool_use.ts"', {input, encoding: 'utf8', timeout: 30000});
  console.log('HOOK EXIT 0 output:', r.slice(0, 500));
} catch (e) {
  console.log('HOOK FAILED:', e.message.slice(0, 500));
  if (e.stdout) console.log('stdout:', String(e.stdout).slice(0, 500));
  if (e.stderr) console.log('stderr:', String(e.stderr).slice(0, 800));
}

