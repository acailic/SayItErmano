# SayItErmano apt repository

This directory is a signed apt repository (suite `stable`, component
`main`) — it is consumed by `apt`, not meant for browsing. To use it on
Ubuntu 24.04:

```bash
curl -fsSL https://acailic.github.io/SayItErmano/apt/key.asc \
  | sudo tee /etc/apt/keyrings/sayit-ermano.asc >/dev/null
echo "deb [signed-by=/etc/apt/keyrings/sayit-ermano.asc] https://acailic.github.io/SayItErmano/apt stable main" \
  | sudo tee /etc/apt/sources.list.d/sayit-ermano.list
sudo apt update && sudo apt install sayit-ermano
```

Installing the [official .deb](https://github.com/acailic/SayItErmano/releases)
configures this repo automatically — the manual setup above is only for
adding the channel to an existing deb install.

Only the latest release is kept in the pool (no downgrades). Full
install/update guide:
[docs/guides/install.md](https://github.com/acailic/SayItErmano/blob/linux/docs/guides/install.md#updates).
