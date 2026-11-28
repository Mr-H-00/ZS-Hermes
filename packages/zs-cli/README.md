# @xerrors/zs

ZS Public v1 command-line client. Requires Node.js 22 or newer.

Install a published package with npm, or follow the [CLI installation guide](../../docs/intro/cli.md#安装) to build and install from this checkout:

```bash
npm install --global @xerrors/zs
zs remote add production https://example.com
zs remote use production
zs login
zs agent list
zs chat --agent default-chatbot
zs kb query --kb-id <id> "your question"
```

Configuration is stored at `~/.zs/config.json` with restrictive file permissions. The CLI supports authentication, Public Agent/Session APIs, terminal chat, attachments, SSE observation, and knowledge-base retrieval. The `thread` commands operate on Public Sessions. Eval, Langfuse, and administration APIs are not included.

Upload a draft before sending a file attachment. Uploading does not create a Session or write to its Workdir. Read the returned `id` and pass it with a message:

```bash
zs file upload ./report.pdf --mime-type application/pdf
zs thread send <session-id> "Summarize the report" --file-id <returned-id>
zs file show <draft-id>
zs file delete <unsubmitted-draft-id>
```

For inline vision input, `thread send --image-url` accepts a complete `data:image/png;base64,...` URL. It preserves the MIME type and does not call private image preprocessing. Draft expiry, submission and recovery follow the [Public API contract](../../docs/advanced/agents-public-api.md#图片与文件附件).

Lists and history are pages: use `--limit`, `--order` and the returned `last_id` as `--after`. Turn lists include work that produced no message.

```bash
zs thread list --limit 20
zs thread history <session-id> --limit 20 --after <last-id>
zs thread turns <session-id>
zs thread send <session-id> "Continue" --idempotency-key <saved-key>
zs thread receipt <session-id> <saved-key>
zs thread input <session-id> <input-id>
zs thread turn <session-id> <turn-id>
```

`send` prints its key before submitting. If the response is lost, it first reads the same key's receipt; retry the original command with that key when reception remains unknown. Input identifies the consumed Turn. Interactive chat reconnects with its cursor and reads that Turn's persistent output, including when no text delta was received. A closed stream or another Turn's completion does not finish the current request. User answers and approvals can continue in the Web interface; cooperation waits remain observed by chat.
