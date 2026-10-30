# githubfollowerbot

GitHub のフォロワーが「どういう人か」を、公開プロフィールから確認するためのツールです。

フォロワー一覧と各フォロワーの公開プロフィール（bio、公開リポジトリ数、フォロワー数、アカウント作成時期など）を取得し、ルールベースのタグ（developer / student / organization / bot-like / inactive / general）を付けて、Markdown レポートにまとめます。タグは公開項目への単純なヒューリスティクスで、人となりを断定するものではありません。

A small tool that profiles a GitHub account's followers from their public profiles and renders a Markdown report with rule-based tags.

## Run (manual)
```
# オンライン（GITHUB_TOKEN があるとレート制限が緩くなります）
GITHUB_TOKEN=... python -m bot.followers --user Arbeit2025 --out followers-report.md

# オフラインのサンプルで試す
python -m bot.followers --fixture examples/sample_profiles.json --out report.md
```

## Run (GitHub Actions)
Actions の「follower-report」ワークフローを手動実行（workflow_dispatch）すると、指定ユーザーのレポートを生成して artifact にアップロードします。

## Auto watch（フォロワーの変化で自動実行）

GitHub Actions には「フォロワーが増えた」イベントが無いので、「follower-watch」ワークフローが1時間に1回フォロワー一覧をポーリングし、リポジトリ内の状態ファイル（`data/followers-state.json`）と比較します。増減があったときだけレポートを再生成し、状態ファイルとレポート（`reports/followers.md`）をリポジトリへ自動コミットします。変化が無ければ何もコミットしません。初回実行はベースライン作成として、全フォロワーのレポートを作ります。検知までの遅延は最大で約1時間＋GitHub スケジューラの遅延です。

手動でも実行できます（workflow_dispatch、対象ユーザーの既定値は Arbeit2025）。ローカルでは次でも試せます。

```
python -m bot.watch --user Arbeit2025
```

## Tags
- organization：Organization アカウント
- bot-like：ログイン名や bio に bot 系の手がかり
- student：bio に学生・大学系のキーワード
- developer：公開リポジトリ5件以上、または bio に開発系キーワード
- inactive：公開リポジトリ0・bio なし・フォロワー0
- general：上記のどれにも当てはまらない
