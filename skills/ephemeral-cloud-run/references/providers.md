# Provider quirks

What runs found out, dated. Check each provider's current docs when a
behaviour below seems to have changed, and add what you find.

## DigitalOcean (2026-11)

- GPU droplets bill per second while they exist, also while powered off:
  destroy them.
- The NVIDIA AI/ML image expects an SSH key registered on the account. A
  token that cannot manage account keys can still boot plain Ubuntu with
  the key in cloud-init `user_data`.
- Without an account SSH key, the root password is created expired, which
  blocks non-interactive key logins. Cancel the expiry in cloud-init
  (`chpasswd: {expire: false}` and `chage -M 99999 -d <today> root` in
  `bootcmd` and `runcmd`).
- Delete: `DELETE /v2/droplets/<id>` answers 204. A later `GET` answering
  404 is the proof that it is gone.

## RunPod (2026-11)

- `rpa_…` API keys work only with the v2 REST API
  (`https://api.runpod.io/v2`); the older GraphQL API rejects them.
- The GPU catalogue (`/catalog/gpus?include=AVAILABILITY`) gives
  availability per data centre, and it changes from minute to minute.
  Reread it before each attempt, and pass the data centres that have
  stock.
- Secure Cloud pods bill per second; a stopped pod still bills for its
  disk. Delete them (`DELETE /pods/<id>`, then 404 on `GET`).
- An image that starts sshd from the `PUBLIC_KEY` environment variable
  saves registering a key.

## Vast.ai (2026-11)

- Billing is per second, with no minimum, which makes short runs cheap.
- Offers come from many hosts. Filter on `verified`, `datacenter`,
  `reliability2`, `cuda_max_good`, `disk_space`, `inet_down` and the total
  price `dph_total`, which includes storage.
- Renting is `PUT /asks/<offer id>/`, and an offer can be gone by the
  time you ask: try the next one.
- A stopped instance still bills for its storage. Destroy it with
  `DELETE /instances/<id>/`; `GET` then returns `{"instances": null}`.
- An SSH key can be registered on the account (`POST /ssh/`) and attached
  to the instance (`POST /instances/<id>/ssh/`); remove the account key
  during teardown.
