"""Word desktop COM bridge. Metadata travels as JSON, never executable code."""
import base64,json,os,subprocess,tempfile
from pathlib import Path
from citations import word_source

SCRIPT=r'''
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$request=Get-Content -LiteralPath $env:MOLTEN_WORD_REQUEST -Raw -Encoding UTF8 | ConvertFrom-Json
$test=$request.action -eq 'test'
$word=$null;$doc=$null
try {
 if ($test) {$word=New-Object -ComObject Word.Application;$doc=$word.Documents.Add();$doc.Content.Text='Preserved text';$doc.Content.Select()}
 else {$word=[Runtime.InteropServices.Marshal]::GetActiveObject('Word.Application');if($word.Documents.Count -eq 0){throw '请先在 Word 中打开一个可编辑文档。'};$doc=$word.ActiveDocument}
 if($doc.ReadOnly -or $doc.ProtectionType -ne -1){throw '当前 Word 文档不可编辑。'}
 function Cite {
  $exists=$false
  foreach($source in $doc.Bibliography.Sources){if($source.Tag -eq $request.tag){$exists=$true}}
  if(-not $exists){$doc.Bibliography.Sources.Add([string]$request.xml)}
  $range=$word.Selection.Range.Duplicate;$range.Collapse(0)
  $field=$doc.Fields.Add($range,-1,('CITATION '+$request.tag),$false);[void]$field.Update()
  $field.Result.Select();$word.Selection.Collapse(0)
 }
 function Bibliography {
  $found=$false
  foreach($field in $doc.Fields){if($field.Code.Text.Trim().StartsWith('BIBLIOGRAPHY')){[void]$field.Update();$found=$true}}
  if(-not $found){$range=$word.Selection.Range.Duplicate;$range.Collapse(0);$field=$doc.Fields.Add($range,-1,'BIBLIOGRAPHY',$false);[void]$field.Update()}
 }
 if($test){Cite;Cite;$word.Selection.TypeParagraph();Bibliography;Bibliography
  $result=@{ok=$true;sources=$doc.Bibliography.Sources.Count;fields=$doc.Fields.Count;preserved=$doc.Content.Text.Contains('Preserved text');codes=@($doc.Fields | ForEach-Object {$_.Code.Text})}
  $doc.SaveAs2([string]$request.output,16)
 } else {
  if($request.action -eq 'cite'){Cite} elseif($request.action -eq 'bibliography'){Bibliography} else {throw '未知 Word 操作。'}
  $result=@{ok=$true;document=$doc.Name}
 }
 $result | ConvertTo-Json -Compress -Depth 5
} catch {@{ok=$false;error=$_.Exception.Message} | ConvertTo-Json -Compress}
finally {if($test -and $doc){$doc.Close(0)};if($test -and $word -and $word.Documents.Count -eq 0){$word.Quit(0)}}
'''

def word_action(action,paper=None,output=None):
    request={'action':action}
    if paper:request['tag'],request['xml']=word_source(paper)
    if output:request['output']=str(Path(output).resolve())
    with tempfile.TemporaryDirectory(prefix='molten_word_') as tmp:
        path=Path(tmp)/'request.json';path.write_text(json.dumps(request,ensure_ascii=False),'utf-8')
        env=dict(os.environ,MOLTEN_WORD_REQUEST=str(path))
        shell=Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
        try:
            r=subprocess.run([str(shell),'-NoProfile','-NonInteractive','-EncodedCommand',base64.b64encode(SCRIPT.encode('utf-16le')).decode()],env=env,capture_output=True,timeout=60,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except subprocess.TimeoutExpired:raise RuntimeError('Word 响应超时，请检查对话框和文档，确认是否已插入后再操作。')
        try:result=json.loads(r.stdout.decode('utf-8-sig').strip())
        except ValueError:raise RuntimeError('无法连接 Word 桌面端，请打开 Word 文档并关闭阻塞的对话框。')
        if not result.get('ok'):raise RuntimeError('Word：'+result.get('error','连接失败'))
        return result
