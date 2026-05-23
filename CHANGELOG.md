# 更新日志

版本号遵循语义化版本(主.次.补丁)。发版流程:

1. 在本文件顶部加一条新版本记录(`## vX.Y.Z — 日期` 加改动条目)
2. 运行 `bash deploy/release.sh X.Y.Z` —— 自动跑测试、写版本号、提交、
   打 tag、推送 main 与 tag、生成 `dist/zimport-tools-X.Y.Z.tar.gz`
