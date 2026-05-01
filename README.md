# Notion Append Page (`notion-append-page`)

Action adapter that appends **one paragraph block** to a Notion page via the Notion API.

## What it does

- Calls Notion API `PATCH /v1/blocks/{block_id}/children`
- Appends a single `paragraph` block containing your `content`
- Uses `NOTION_API_KEY` from the environment (Notion integration token)

## Configuration (never commit secrets)

Set the token in your environment:

```powershell
$env:NOTION_API_KEY = "<notion_integration_token>"
```

You must also share the target Notion page with the integration in Notion.

## Input

```json
{
  "page_id": "3535ef606f0180e3b174e27f6c1b45a4",
  "content": "This is a note appended by the agent."
}
```

## Local checks

```powershell
siglume test .
siglume score . --offline
```

`siglume test .` uses `dry_run` and does **not** call Notion.

