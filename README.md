# Enigma — 本地数据脱敏工具

把表格里的敏感字段（如游戏名、玩法）替换成 `GAME_0001` 这类占位符，
交给 ChatGPT/Claude 处理后，再用本工具一键还原。**所有过程纯本地，密钥不出本机。**

## 安装与运行（开发模式）

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## 打包成可执行文件

```bash
pip install pyinstaller
python build/build.py
# 产物在 dist/ 目录
```

## 工作流

1. **新建项目** → 选择 `.keyfile` 保存位置 → 设置密码（密码丢了无法还原，请记牢）
2. **脱敏**：选数据表 → 勾选要脱敏的列、给每列一个前缀（如 `GAME` / `TYPE`）→ 一键脱敏
3. 把生成的 `*_encrypted.xlsx` 上传给 AI；同时复制工具生成的 Prompt 模板贴到对话开头
4. **还原**：把 AI 处理完的文件喂回工具 → 一键还原

## 文件目录

```
项目所在目录/
├─ myproject.keyfile      # 加密的密钥文件（核心）
├─ encrypted/             # 工具产生的脱敏后文件
└─ decrypted/             # 工具产生的还原后文件
```

## 安全说明

> ⚠️ **重要：脱敏文件 ≠ 加密文件**
>
> `encrypted/` 目录下的文件是**已脱敏的明文**，不是加密文件。
> 它包含真实的非敏感字段（数字、日期、未勾选的列）和占位符（GAME_xxx）。
> 可以放心给 AI，但**不应公开发布**——结合其他信息仍可能反推业务。
> 真正受密码保护的只有 `.keyfile`，丢了脱敏文件不会泄露映射关系。

- 密钥文件用 AES-128 + HMAC（Fernet）+ PBKDF2-HMAC-SHA256（48 万次迭代）加密
- 密码至少 8 位（弱密码可被离线 GPU 爆破）
- 工具不发起任何网络连接
- 跨文件一致：同一项目内，相同的原值得到相同的 Token
- 还原阶段宽容匹配：容忍大小写、空格、引号、末尾标点
- AI 编造的不存在 Token 会被标记 `[⚠未识别]` 而不是误还原
