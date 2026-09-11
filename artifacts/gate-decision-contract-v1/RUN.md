# 安装与复核

仅新增artifacts/gate-decision-contract-v1文件；按ZIP相对路径添加，同名文件存在先比较，勿覆盖不同内容。
CONTRACT.md定义决策语义；REVIEW.md是20行复核表；review.json包含依据原文；manifest.json记录契约、标签草案、门控代码哈希。

PowerShell在项目根目录：

```powershell
$manifest = Get-Content '.\artifacts\gate-decision-contract-v1\manifest.json' -Raw | ConvertFrom-Json
foreach ($entry in $manifest.files.PSObject.Properties) {
    $actual = (Get-FileHash -Path $entry.Name -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $entry.Value) { throw "文件指纹不一致：$($entry.Name)" }
}
Write-Output '冻结文件校验通过；19 条配对 + 1 条不适用，等待人工确认'
```

按字节校验，换行差异也阻断；发现差异先比较，不重算指纹绕过。
本步没有评测成绩；结构检查为20条（11允许、8拒绝、1不适用），未运行查询。
请确认CONTRACT.md与20条草案，或指出编号和理由；确认后另存带哈希的确认记录。
旧冻结标签不覆盖，确认之后才考虑新门控实现。
主要风险：人工复核仍可能不完备；当前没有不确定样本；通用指代和否定处理不能由本表证明。
Git建议只提交本目录新增文件：docs(retrieval): define gate decision contract for review
未提交、推送或切换运行时。
