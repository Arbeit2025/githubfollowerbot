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

## followme-lite（リポジトリ発見→採点→follow/star候補）

yumiaura/followme の「仕組み」に着想を得た軽量版です（本家にライセンスが無いためコードは複製せず、パイプラインの形と既定の閾値だけを引き継いだ独自実装）。本家との違いは、clone＋ローカルLLM（Ollama）での採点を、公開メタデータだけを使うルールベース採点に置き換えたことです。そのため GitHub Actions でもそのまま動きます。

流れ：GitHub Search で最近更新されたリポジトリを取得 → 説明文・トピック・スター・フォーク・更新の新しさ・サイズ・ライセンスから `idea` / `skill` を各1.0〜10.0で採点 → 合計が **14超でfollow候補、16超でstar候補**（本家の既定値と同じ）。

```
# 候補レポートだけ（dry-run、既定。follow/star は実行しません）
python -m bot.discover --fixture examples/sample_repos.json --out report.md
python -m bot.discover --count 5 --language Python --out reports/discover.md

# 実際に follow/star する（個人トークンが必要。ローカルでのみ）
GITHUB_TOKEN=... python -m bot.discover --apply
```

安全策：既定は dry-run で状態ファイルも変更しません。`--apply` のときだけ実行し、1回あたりの上限は follow 5件・star 10件まで。fork・アーカイブ済み・botアカウント・自分のリポジトリは除外し、実行済みは状態ファイル（`data/discover-state.json`）に記録して二度は実行しません。Actions の「followme-discover」ワークフローは手動実行のみで、常に dry-run のレポートを artifact に出すだけです（定期的な自動follow/starはしません）。

## followback（フォローバックが無い相手のフォロー解除）

`bot.discover --apply` でフォローした相手だけを対象に、フォローした日時を状態ファイルへ記録します。その後、`bot.followback` が「フォローから31日以上経ち、相手がフォローバックしていない」相手を解除候補にします。

```
# 候補レポートだけ（dry-run、既定。解除は実行しません）
python -m bot.followback --self Arbeit2025

# 実際に解除する（個人トークンが必要。ローカルでのみ、1回5件まで）
GITHUB_TOKEN=... python -m bot.followback --self Arbeit2025 --apply
```

安全策：対象はこのツールがフォローした相手のみで、手動でフォローした相手には触りません。フォロー日時が記録されていない相手（この機能より前の状態）は「日数が証明できない」ため自動解除の対象外です。解除した相手は状態ファイルの履歴に残り、discover が再フォローすることもありません。Actions の「followback-check」ワークフローは手動実行のみで、常に dry-run のレポートを artifact に出すだけです。

## Tags
- organization：Organization アカウント
- bot-like：ログイン名や bio に bot 系の手がかり
- student：bio に学生・大学系のキーワード
- developer：公開リポジトリ5件以上、または bio に開発系キーワード
- inactive：公開リポジトリ0・bio なし・フォロワー0
- general：上記のどれにも当てはまらない
