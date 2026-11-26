# Chem Paper Daily

化学の新着論文を毎朝1本選び、Claude が英語の紹介文（Background / What they did / Key results / Why it matters、日本語の要点、重要単語、考えるための問い）を書いて Slack に届けるアプリです。
ブラウザ（自分の PC の中だけで動く Webアプリ）で、過去の紹介の検索、全文へのリンク（DOI・東北大の学外アクセス・オープンアクセス版）、読んだ論文の PDF とメモの管理（本棚）ができます。

- 論文の情報源: Crossref、ChemRxiv、PubMed（無料の公開 API）
- 紹介文の生成: 契約中の Claude プランを Claude Code 経由で利用（設定で従量課金の API 方式にも切り替え可）
- 動作環境: Windows 10 / 11。セットアップはダブルクリック中心です

## はじめかた

**[docs/SETUP_GUIDE.md](docs/SETUP_GUIDE.md)** の手順に沿って進めてください。

| ファイル | 役割 |
|---|---|
| `setup.bat` | 初回セットアップ（ライブラリのインストール、毎朝 7:00 の自動実行の登録） |
| `start.bat` | Webアプリを起動してブラウザで開く（http://127.0.0.1:8765） |
| `run_daily.bat` | 毎朝のジョブ（論文選び → 紹介文 → Slack）。手動でダブルクリックするとテストできます |
| `config.toml` | 雑誌リスト、キーワード、ジャンル配分、通知時刻などの設定 |
| `.env` | Slack の Webhook URL など秘密の設定（Git には入りません） |

データ（`data/` フォルダ: データベース、PDF、ログ）は PC の中だけに保存され、Git には入りません。

## 開発者向け

```
uv sync --extra dev
uv run python -m pytest -q
uv run ruff check app tests
uv run python -m app.web.main      # Webアプリ（127.0.0.1:8765）
uv run python -m app.daily_job     # 毎朝のジョブを1回実行
```
