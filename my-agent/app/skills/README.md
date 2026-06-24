# skills/

このフォルダに **Anthropic Agent Skills 形式**のスキルを配置します。
`app/agent.py` が起動時にこのフォルダを走査し、`SKILL.md` を持つサブフォルダを
すべてスキルとして読み込みます（`SkillToolset` 経由、Gemini から利用可能）。

## 置き方

別PCで作成したスキルフォルダを、このディレクトリ直下にそのままコピーしてください。

```
app/skills/
├── pptx/
│   ├── SKILL.md          # 必須（YAML frontmatter: name, description ＋ 本文の手順）
│   ├── scripts/          # 任意（python-pptx 等の実行スクリプト）
│   └── references/       # 任意（参照資料・テンプレート）
└── narrative/
    ├── SKILL.md
    └── ...
```

## 注意

- 各スキルフォルダには **`SKILL.md` が必須**です（無いフォルダは読み込まれません）。
- スキルの `scripts/` が使う Python ライブラリ（例: `python-pptx`, `openpyxl`）は、
  `my-agent/pyproject.toml` の依存に追加が必要です（`uv add <pkg>` → `agents-cli install`）。
- スクリプトは `UnsafeLocalCodeExecutor` でコンテナ内実行されます。信頼できる社内製
  スキルのみを置いてください。
