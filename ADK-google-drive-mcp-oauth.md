# Google Drive MCP 接続（OAuth）導入ガイド

> 既存の ADK エージェント（Agent Runtime にデプロイ済み・Gemini Enterprise に登録済み・Platform UI から会話可能）に、
> **Google 公式のリモート Drive MCP サーバ**への接続を追加するための設計・手順まとめ。
>
> 対象バージョン: **google-adk 2.3.0**（`.venv` / `uv.lock` で確認済み）
>
> ⚠️ 出典について: 一部の項目はこの環境から `docs.cloud.google.com`（公式ドキュメント本文）へ直接到達できなかったため、
> Google Cloud Blog / Google Developers（Drive MCP 設定ページ）/ Google Codelab / 技術記事を根拠にしています。
> 「未検証」と明記した箇所は実装時に実機で確定してください。

---

## 0. ゴール

Platform UI で会話しているユーザー本人の権限で、エージェントが Google Drive を検索・読み取りできるようにする。
（例: 「私の Drive から先週の資料を探して要約して」）

---

## 1. 全体像（これだけ理解すれば OK）

いちばん大事な原則:

> **エージェントは OAuth の同意フローを自分で実装しない。**
> Gemini Enterprise（以下 GE）がユーザーの同意取得・トークン発行・更新・保管を丸ごと代行し、
> **エンドユーザーのアクセストークンをエージェントの `state` に注入**する。
> エージェントの仕事は「注入されたトークンを読んで、Drive MCP に `Authorization: Bearer` で渡す」だけ。

```
[Platform UI でユーザーが会話]
        │
        ▼
[Gemini Enterprise]
   ① ユーザーに「Authorize」ボタン＋Google 同意画面を表示（Authorization リソースの手順書に従う）
   ② 3-legged OAuth を実行し、ユーザーのアクセストークン（＋リフレッシュトークン）を取得
   ③ リフレッシュトークンをサーバー側で保管し、失効時は自動で再発行（← 全部 GE がやる）
   ④ 有効なアクセストークンを、リクエストのたびに state["temp:AUTH_ID"] に注入
        │
        ▼
[Agent Runtime 上の ADK エージェント]
   header_provider が state からトークンを読み、Bearer ヘッダを組み立てる
        │
        ▼
[Google Drive リモート MCP サーバ]  https://drivemcp.googleapis.com/mcp/v1
   ユーザー本人の権限で Drive にアクセス（search_files / read_file_content 等）
        │
        ▼
   結果がエージェントに返る
```

**アクセスするのはエージェントの実行用サービスアカウントではなく「会話しているユーザー本人」。**
→ だから Agent Runtime のサービスアカウントに Drive 権限を足す必要はない。

---

## 2. 重要な概念整理（初心者向け）

### 2-1. OAuth クライアント と Authorization リソースは「別物」

| | OAuth クライアント | Authorization リソース |
|---|---|---|
| どこにある | Google Cloud（Google Auth platform → Clients） | Gemini Enterprise の中（Discovery Engine `authorizations`） |
| 正体 | アプリの身分証＋鍵（`client_id` / `client_secret`） | 設定レコード（鍵の使い方メモ）。**サーバーではない** |
| 役割 | 「このアプリは正規」と Google に証明 | GE に「同意フローの回し方」を教える |
| 例え | 会社の実印＋印鑑登録 | 受付に貼る「この案件はこの実印でこう手続き」手順書 |
| 作る回数 | 基本 1 回（使い回せる） | 権限セットごとに 1 つ（Drive 用に 1 つ） |

**関係:** Cloud で作った OAuth クライアント（鍵）の情報を**書き写して包んだもの**が Authorization リソース。
GE が「あなたの代理」で OAuth を回すために手元に持っておく設定情報。

### 2-2. `AUTH_ID` は 3 つの役割を兼ねる

Authorization リソースのフルネームは
`projects/PROJECT_ID/locations/global/authorizations/AUTH_ID`。
末尾の `AUTH_ID` は自分で決める任意の名前（例: `drive-auth`）。同じ値が 3 か所に登場する:

| 使う場所 | 役割 |
|---|---|
| publish（登録）の `--authorization-id AUTH_ID` | GE に「この認可が必要」と宣言 |
| 実行時 `tool_context.state["temp:AUTH_ID"]` | 注入されたトークンを取り出すキー |
| Authorization リソースの名前 | 上記 2 つが指す実体 |

### 2-3. トークンの保存は「作らない」— GE が全部やる

| | 誰が持つ | 寿命 | あなたの作業 |
|---|---|---|---|
| リフレッシュトークン（長期） | **GE がサーバー側で保管**（ユーザー×AUTH_ID ごとに隔離） | 長い | **見ない・保存しない・何もしない** |
| アクセストークン（短期・実際に使う鍵） | 実行時に `state["temp:AUTH_ID"]` に注入 | 約 1 時間 | **state から読むだけ・保存しない** |

- 保管場所・再発行（リフレッシュ）・ユーザーごとの分離・同意取り消しは **全部 GE が代行**。DB もキャッシュも作らない。
- **アクセストークンを自分でキャッシュしないこと。** 1 時間で失効する。毎回 `state` から読めば常に有効なものが入っている。

---

## 3. deploy と publish は「別作業」（勘違いしやすい点）

`authorizations`（＝どの認可が必要か）は **agent.py には書かない。** publish（登録）時に指定する。

```
【deploy】 agent.py を Agent Runtime に載せる
   └─ ここに書くのは McpToolset と header_provider（トークンを"使う"側）だけ
        ※ authorizations は書かない

【publish】 GE にエージェントを登録する
   └─ ここで --authorization-id AUTH_ID を渡す（＝「この手順書を使え」と宣言）
```

- **deploy** = トークンを**使う**プログラムを置く
- **publish** = トークンを**用意させる**指示を出す

GE が「認可が必要」と動くのは、この publish 時の宣言が根拠。実際にユーザーへ同意画面を出すのは**ユーザーが初回に会話した瞬間**。

---

## 4. やるべきこと（チェックリスト）

### A. Google Cloud 側
- [ ] OAuth クライアント（Web application）を作成
- [ ] OAuth 同意画面（Data Access）に Drive スコープを追加
- [ ] API を有効化: `drive.googleapis.com` と `drivemcp.googleapis.com`

### B. Gemini Enterprise 側
- [ ] Authorization リソース（`AUTH_ID`）を作成（A の OAuth クライアント情報を登録）

### C. ADK コード側（このリポジトリ）
- [ ] `pyproject.toml`: `google-adk[gcp]` → `google-adk[gcp,mcp]`
- [ ] `app/agent.py`: Drive MCP の `McpToolset` ＋ `header_provider` を追加
- [ ] `app/.env`: `GEMINI_AUTH_ID` を追加

### D. デプロイ & 登録
- [ ] `agents-cli deploy`（再デプロイ）
- [ ] `agents-cli publish gemini-enterprise --authorization-id AUTH_ID ...`（認可を紐付けて再登録）
- [ ] 動作確認（`search_files` を 1 回叩いて 200/401 を見る）

---

## 5. ADK コード側の具体的変更

### 5-1. `pyproject.toml`（必須: `mcp` extra 追加）

`.venv` を確認したところ `mcp` パッケージは**未インストール**（`McpToolset` の動作に必要）。

```toml
# 変更前
"google-adk[gcp]>=2.0.0,<3.0.0",
# 変更後
"google-adk[gcp,mcp]>=2.0.0,<3.0.0",
```

反映: `agents-cli install`（または `uv sync`）。

### 5-2. `app/agent.py`（Drive MCP toolset を追加）

`root_agent` の `tools=[...]` に Drive MCP を足す。追加コードのイメージ:

```python
import os
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.adk.agents.readonly_context import ReadonlyContext

DRIVE_MCP_URL = "https://drivemcp.googleapis.com/mcp/v1"
AUTH_ID = os.environ["GEMINI_AUTH_ID"]  # Authorization リソースの ID（.env で設定）


def _drive_auth_headers(ctx: ReadonlyContext) -> dict[str, str]:
    """GE が注入したエンドユーザーのアクセストークンを毎回 state から読み、Bearer にする。
    ※ トークンは短命なので絶対に自前でキャッシュしない。
    """
    token = ctx.state.get(f"temp:{AUTH_ID}")
    return {"Authorization": f"Bearer {token}"} if token else {}


drive_mcp = McpToolset(
    connection_params=StreamableHTTPConnectionParams(url=DRIVE_MCP_URL),
    header_provider=_drive_auth_headers,
    # 必要なツールだけ公開するのが安全（8 個中、まずは読み取り系）
    tool_filter=["search_files", "read_file_content", "get_file_metadata"],
)

# root_agent の tools に drive_mcp を追加する
```

補足:
- エージェント定義は**同期**（async にしない）。
- リモート MCP なので **npx / Node.js 不要** → `.devcontainer/Dockerfile` の変更は不要。
- Drive MCP のトランスポートは **Streamable HTTP**（`POST /mcp`=JSON-RPC, `GET /mcp`=SSE streaming）。→ `StreamableHTTPConnectionParams` が正しい。

### 5-3. `app/.env`

```dotenv
GEMINI_AUTH_ID=drive-auth   # Authorization リソースで決めた AUTH_ID と一致させる
```

client_id / client_secret は**エージェント側に置かない**（同意フローは GE が持つため）。

### Drive MCP が公開するツール（8 個・参考）
`search_files` / `read_file_content` / `download_file_content` / `get_file_metadata` /
`get_file_permissions` / `list_recent_files` / `create_file` / `copy_file`

---

## 6. Google Cloud 側の具体手順

### 6-1. OAuth クライアント作成
Google Cloud Console → **Google Auth platform → Clients** → Create client
- Application type: **Web application**
- Authorized redirect URIs（GE 用の固定値）:
  - `https://vertexaisearch.cloud.google.com/oauth-redirect`
  - `https://vertexaisearch.cloud.google.com/static/oauth/oauth.html`
- 発行された **Client ID / Client secret** を控える。

### 6-2. 同意画面（スコープ）
Google Auth platform → **Data Access** に Drive スコープを追加:
- `https://www.googleapis.com/auth/drive.readonly`
- `https://www.googleapis.com/auth/drive.file`

（Drive MCP が要求するスコープ。読み取り中心なら `drive.readonly`、ファイル作成もするなら `drive.file` も。）

### 6-3. API 有効化
```bash
gcloud services enable drive.googleapis.com
gcloud services enable drivemcp.googleapis.com
```

### 6-4. Authorization リソース作成（Gemini Enterprise / Discovery Engine）
コンソールのエージェント編集画面の Authorization セクション、または Discovery Engine API で作成。API の場合のボディ（フィールド名は検証済み）:

```jsonc
{
  "serverSideOauth2": {
    "clientId":         "OAUTH_CLIENT_ID",     // 6-1 の値
    "clientSecret":     "OAUTH_CLIENT_SECRET", // 6-1 の値
    "authorizationUri": "https://accounts.google.com/o/oauth2/v2/auth?client_id=OAUTH_CLIENT_ID&redirect_uri=https%3A%2F%2Fvertexaisearch.cloud.google.com%2Fstatic%2Foauth%2Foauth.html&scope=<URLエンコードしたDriveスコープ>&access_type=offline&prompt=consent&response_type=code",
    "tokenUri":         "https://oauth2.googleapis.com/token"
  }
}
```
- リソース名: `projects/PROJECT_ID/locations/global/authorizations/AUTH_ID`
- `AUTH_ID` は `.env` の `GEMINI_AUTH_ID` と一致させる。

---

## 7. デプロイ & 登録

```bash
# 5〜6 の変更後、再デプロイ
agents-cli deploy

# 認可を紐付けて再登録（idempotent＝上書き更新）
agents-cli publish gemini-enterprise \
  --agent-runtime-id  "<あなたの reasoningEngines リソース名>" \
  --gemini-enterprise-app-id "<あなたの engines リソース名>" \
  --authorization-id  "drive-auth"   # ← これが authorizations への宣言
```
- `agent-runtime-id` / `gemini-enterprise-app-id` は既存の登録から取得（`deployment_metadata.json` があれば自動検出。現状このファイルは `"None"` なので、実際の値を確認して指定）。

### Agent Runtime 側の特別な設定は？
- **基本、通常の再デプロイだけで OK。** OAuth 用の Runtime 固有設定・サービスアカウント権限追加は不要。
- 依存に `mcp` を足したので、再ビルドで取り込まれることだけ確認。

---

## 8. 動作確認
1. Platform UI でエージェントに Drive 関連の依頼をする。
2. 初回は「Authorize」ボタン → Google 同意画面が出る → 許可。
3. `search_files` 等が動けば成功。失敗時は下記トラブルシュート。

| 症状 | 原因の当たり |
|---|---|
| 同意画面が出ない | publish で `--authorization-id` を付け忘れ（＝GE が認可必要と知らない） |
| 401 Unauthorized | `state["temp:AUTH_ID"]` が空／`AUTH_ID` 不一致／スコープ不足 |
| 403 | Drive スコープ不足、または API 未有効化 |

---

## 9. 未検証・要注意（実装時に確定すること）

| 項目 | 状態 | 対応 |
|---|---|---|
| state キー `temp:AUTH_ID` | ほぼ確定（複数の Google 系情報が一致） | 初回に `ctx.state` のキー一覧をログ出力して実物を確認すると確実 |
| Bearer ヘッダで渡す | 事実上確定（公式は wire 詳細を明記せず） | 実機で確認 |
| **drivemcp が GE 発行トークンを受理するか（audience/allowlist）** | **唯一の実証ポイント** | `search_files` を 1 回叩いて 200/401 を確認。401 なら OAuth クライアント/スコープ整合を見直す |

---

## 10. 用語ミニ辞典

- **MCP（Model Context Protocol）**: AI エージェントが外部ツール/データに繋ぐための標準プロトコル。
- **リモート MCP サーバ**: ネット越しに使う MCP サーバ（今回の Drive MCP）。ローカルに何もインストールしない。
- **3-legged OAuth**: 「アプリ」「ユーザー」「Google」の三者で行う同意フロー。ユーザー本人が許可する方式。
- **アクセストークン / リフレッシュトークン**: 実際に API を叩く短命の鍵 / それを再発行するための長期の鍵。
- **header_provider**: `McpToolset` の引数。リクエストごとに HTTP ヘッダを組み立てるコールバック。ここで Bearer を注入。
- **Agent Runtime**: エージェントの実行基盤（旧称 Agent Engine）。
- **Gemini Enterprise (GE)**: エージェントをユーザーに提供するプラットフォーム。OAuth 同意も代行する。

---

## 11. 参考リンク
- [Configure the Drive MCP server — Google for Developers](https://developers.google.com/workspace/drive/api/guides/configure-mcp-server)
- [MCP Reference: drivemcp.googleapis.com — Google for Developers](https://developers.google.com/workspace/drive/api/reference/mcp)
- [Announcing official MCP support for Google services — Google Cloud Blog](https://cloud.google.com/blog/products/ai-machine-learning/announcing-official-mcp-support-for-google-services)
- [Register and manage ADK agents on Gemini Enterprise Agent Platform — Google Cloud Docs](https://cloud.google.com/gemini/enterprise/docs/register-and-manage-an-adk-agent)
- [Integrate Gemini Enterprise Agents with Google Workspace — Google Codelabs](https://codelabs.developers.google.com/ge-gws-agents)
- [Guide: Registering ADK Agents on Vertex AI Agent Engine with Gemini Enterprise — cloudbabble](https://www.cloudbabble.co.uk/2025-12-08-Registering-ADK-Agents-On-Vertex-AI-Agent-Engine-In-Gemini-Enterprise/)

---

## 12. Gemini Enterprise 側 OAuth 設定の公式ページ（重点）

GE 側の OAuth（Authorization リソース）設定を「ドンピシャ」で押さえたいときに読むページ。
（1・2 は上の「11. 参考リンク」と重複するが、OAuth 設定用途としてここに再掲。）

1. **【本命・公式ドキュメント】Register and manage ADK agents hosted on Gemini Enterprise Agent Platform**
   https://cloud.google.com/gemini/enterprise/docs/register-and-manage-an-adk-agent
   OAuth クライアント作成 → Authorization リソース作成 → `authorizations` で登録 → 実行時 `temp:AUTH_ID` でトークン取得、まで一次情報でカバー。
2. **【本命・公式ハンズオン Codelab】Integrate Gemini Enterprise Agents with Google Workspace**
   https://codelabs.developers.google.com/ge-gws-agents
   コンソールの **Authorizations** セクション設定（Authorization name / Client ID / Client secret / Token URI）を画面付きで手順化。Drive など Workspace アクセスが題材。
3. **Register and manage A2A agents（Authorization リソース作成の curl・手順共通）**
   https://cloud.google.com/gemini/enterprise/docs/register-and-manage-an-a2a-agent
4. **Authenticate to Gemini Enterprise（認証概要）**
   https://cloud.google.com/gemini/enterprise/docs/authentication
5. **Managing access for deployed agents**
   https://cloud.google.com/gemini-enterprise-agent-platform/scale/runtime/manage-agent-access

> 注意: この環境からは `docs.cloud.google.com`（上記 cloud.google.com リンクのリダイレクト先）へ到達できず本文取得は未検証。URL は検索結果で確認したもので、ブラウザからは正常に開けるはず。Codelab（2）は当環境からも取得成功・内容確認済み。
