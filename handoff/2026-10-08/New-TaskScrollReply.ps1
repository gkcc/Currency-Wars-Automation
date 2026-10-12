param(
    [Parameter(Mandatory = $true)][string]$Python,
    [Parameter(Mandatory = $true)][string]$RequestFile,
    [Parameter(Mandatory = $true)][string]$ChapterText,
    [Parameter(Mandatory = $true)][string]$OutputFile,
    [string]$TargetText = '0/2',
    [int]$Delta = -120
)

# Local plan construction only. The original manual-step CLI owns submission.
# Never infer a chapter label or copy an old capture/epoch into a new request.
$cwRepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$cwBuilder = @'
import json
from pathlib import Path
import sys

root, request_file, chapter_text, target_text, delta, output_file = sys.argv[1:]
sys.path.insert(0, str(Path(root) / 'tools'))
import currency_wars_runner as runner
from currency_wars_visual_guards import TASK_LIST_SCROLL_CONTROL

request = json.loads(Path(request_file).read_bytes().decode('utf-8-sig'))
observed = request['observation']
def unique(label):
    row = runner.find_text(observed['rows'], label, exact=True)
    if row is None:
        raise ValueError('Current complete text is absent or ambiguous: ' + label)
    return row

unique('\u521b\u4e1a\u6307\u5357')
target, chapter = unique(target_text), unique(chapter_text)
bounds = list(target['box'])
action = dict(type='scroll', args=[(bounds[0]+bounds[2])/2, (bounds[1]+bounds[3])/2, int(delta)],
    expected_page=observed['page'], guard_texts=['\u521b\u4e1a\u6307\u5357', chapter['text'], target['text']],
    target_evidence=dict(control_id=TASK_LIST_SCROLL_CONTROL, source='observed_screen',
        snapshot_id=request['snapshot_id'], capture_request_id=observed['capture_request_id'],
        frame_id=observed['frame_id'], text=target['text'], bounds=bounds,
        chapter_text=chapter['text'], chapter_bounds=list(chapter['box'])),
    reason='\u6eda\u52a8\u5f53\u524d\u521b\u4e1a\u6307\u5357\u7684\u975e\u5173\u952e\u4efb\u52a1\u5217\u8868\uff0c\u6838\u5b9e\u7ae0\u8282\u672b\u5c3e\u9886\u53d6\u72b6\u6001\uff1b\u4e0d\u5ba3\u544a\u4efb\u52a1\u5b8c\u6210')
reply = {key: request[key] for key in ('request_id', 'snapshot_id', 'resume_epoch')}
reply['actions'] = [action]
runner.validate_plan(reply, request, request['resume_epoch'])
output = Path(output_file).resolve()
with output.open('xb') as stream:
    stream.write((json.dumps(reply, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
print(json.dumps(dict(reply_file=str(output), request_id=request['request_id'],
    target_text=target['text'], chapter_text=chapter['text'], args=action['args'],
    input_submitted=False), ensure_ascii=False))
'@

& $Python -B -X utf8 -c $cwBuilder $cwRepoRoot $RequestFile $ChapterText $TargetText $Delta $OutputFile
if ($LASTEXITCODE -ne 0) {
    throw 'Plan construction failed before submission; no Worker job or game input was created.'
}
