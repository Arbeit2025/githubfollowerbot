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

## Tags
- organization：Organization アカウント
- bot-like：ログイン名や bio に bot 系の手がかり
- student：bio に学生・大学系のキーワード
- developer：公開リポジトリ5件以上、または bio に開発系キーワード
- inactive：公開リポジトリ0・bio なし・フォロワー0
- general：上記のどれにも当てはまらない
