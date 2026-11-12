---
name: secret-store
description: Get an API token, key or password from the user into a private file on this machine without it passing through the chat. Use whenever a task needs a credential the user has to supply (a cloud or API token, an account key, a password), when the user offers to paste one, or right after they pasted one into the chat.
---

# Secret store

A secret pasted into the chat is in the session's transcript and is sent
to the model provider. `scripts/secret-store` (in this skill's folder;
stdlib Python 3) stores a named secret in a private file instead. It
prompts without echo and never prints the value. Files live under
`~/.config/secret-store/` (or `$SECRET_STORE_DIR`), one per name. The
folder is mode 700 and each file mode 600.

| Command | What it does |
| --- | --- |
| `secret-store NAME` | Prompts for the secret with the input hidden, or reads it from a pipe, and stores it. |
| `secret-store NAME --clipboard` | Takes it from the clipboard (`wl-paste`, `xclip` or `xsel`). |
| `secret-store --path NAME` | Prints the file a program reads the secret from. |
| `secret-store --list` | Lists the stored names with size and age, never the values. |
| `secret-store --rm NAME` | Deletes one. |

After storing, it prints the name, the length and the first 8 hex digits of
the value's SHA-256, so the user can check that the right value arrived.
The file holds the value exactly: one line ending from the prompt or pipe is
dropped, nothing else, and no newline is added.

## Steps

1. When a task needs a credential, ask for it before the user pastes it.
   Pick a NAME for it (for example `cloudflare-token`). Give the user this
   command, with the absolute path of the script, to run in a terminal on
   this machine:

   ```bash
   python3 <skill folder>/scripts/secret-store NAME
   ```

   If they are at this machine but not in a terminal, they can copy the
   secret and tell you, and you run it with `--clipboard`. Never ask the
   user to type or paste a secret into the chat, and never put one on a
   command line, where it lands in the transcript and in `ps`.
2. Use the secret from the file only:
   - pass the path to a program that reads a file;
   - or read it inside the command that uses it, for example
     `TOKEN=$(cat "$(python3 <skill folder>/scripts/secret-store --path NAME)") some-cli ...`, so the
     value itself never appears in the command.

   Never print it, echo it or write it into another file, a commit or a
   memory. To check that it is there, use `--list`, or compare the length
   and hash prefix it printed when it was stored.
3. If the user pasted a secret into the chat anyway:
   - store it at once with `secret-store NAME`, giving the value on
     standard input, never as an argument;
   - from then on use only the file, and never repeat the value;
   - tell the user once that it is now in this session's transcript, and
     suggest rotating it when the task is done.
4. When the task is over and the secret is no longer needed, offer to
   delete it with `--rm`.

The script never sends the secret anywhere. A program the agent starts with
it can, so pass a secret only to the tool the user meant it for.
