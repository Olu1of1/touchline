# Deploying Touchline to Azure

This takes about 20 minutes the first time. Run the commands from the project folder (the one with `Dockerfile` in it), in Windows Command Prompt or any shell. Each command goes on one line.

## 0. Before you start

- **The language model: Azure OpenAI in Microsoft Foundry.** Free-trial subscriptions have no model quota, so upgrade the subscription to Pay-As-You-Go first (Subscriptions → your subscription → Upgrade). Your remaining credit still applies. Then create an **Azure OpenAI** resource (Standard S0, for example in East US 2), open it in Foundry, and deploy **gpt-4.1-mini** as Global Standard. The endpoint and KEY 1 are on the resource's **Keys and Endpoint** page.
- **Azure AI Speech** on the Free (F0) tier gives the neural voice.
- Set a **budget alert** (Cost Management → Budgets). This project costs cents.
- Fill in `.env` and test it locally with `python -m touchline.check`. It should print `azure-openai:...` and `accepted`.
- Install the [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli). Check with `az version`.

## 1. Pre-build the featured matches (locally, once)

```
python -m touchline.prebuild
```

This writes `timelines/M059.json` and the other recommended matches, with co-commentary written by the model and checked by the Verifier. It takes a few minutes per match. Commit these files: the app and the judges both use them.

## 2. Sign in and prepare the subscription

```
az login
az extension add --name containerapp --upgrade
az provider register --namespace Microsoft.App
az provider register --namespace Microsoft.OperationalInsights
```

## 3. Build and deploy in one command

```
az containerapp up --name touchline --resource-group rg-touchline --location eastus2 --source . --ingress external --target-port 8000
```

This builds the image in the cloud with the `Dockerfile` (no Docker needed on your PC), creates a Container Apps environment and a container registry, and prints the app's public URL. Use the same region as your resource group.

## 4. Size it and add your keys as secrets

Keys go in as **secrets**, so they never appear in plain text in the app's settings.

```
az containerapp secret set --name touchline --resource-group rg-touchline --secrets aoai-key=<your Azure OpenAI key> speech-key=<your Speech key>
```

```
az containerapp update --name touchline --resource-group rg-touchline --cpu 0.5 --memory 1.0Gi --min-replicas 0 --max-replicas 2 --set-env-vars AZURE_OPENAI_ENDPOINT=<endpoint> AZURE_OPENAI_DEPLOYMENT=<deployment name> AZURE_OPENAI_API_KEY=secretref:aoai-key AZURE_SPEECH_REGION=<region> AZURE_SPEECH_KEY=secretref:speech-key
```

- **No Speech resource yet?** Leave out `speech-key=...` from the first command and the two `AZURE_SPEECH_*` settings from the second. The player then uses the browser's voice.
- **0.5 CPU and 1 GB** is enough. The app uses about 310 MB.
- **`--min-replicas 0`** scales to zero when nobody is using it, so it costs nothing while idle and stays within the free monthly allowance. The first visit after a quiet spell takes about 15 seconds to wake up. During judging you can set `--min-replicas 1` to remove that delay, at a few dollars a week.

## 5. Check it

Open the URL from step 3. Then:

- `https://<your-app>/api/config` should show `"tts": "azure"` and `"ask": "agent-framework/azure-openai:..."`.
- Play M059, choose "Neural voice (Azure)", press A and ask "how many shots have they had?".

## Updating after code changes

```
az containerapp up --name touchline --resource-group rg-touchline --source .
```

## If something goes wrong

```
az containerapp logs show --name touchline --resource-group rg-touchline --follow
```

- **The model returns 429 (too many requests):** raise the deployment's tokens-per-minute limit in Foundry (Deployments → Edit), or set `TOUCHLINE_LLM_MOMENTS=prematch,half_time,full_time,substitution` to make fewer calls. The app keeps answering from templates and the offline router meanwhile.
- **The model is quota-limited or returns 401:** check the deployment name and key. The app falls back to templates and the offline router, so it keeps working while you fix it.
- **Out of memory:** raise to `--memory 2.0Gi`.

## Cost, roughly

| Piece | Cost |
|---|---|
| Container Apps, scaled to zero | Within the monthly free allowance for light use |
| Container registry (Basic) | A few dollars a month |
| Azure OpenAI (gpt-4.1-mini) | Cents. A whole pre-built match is about 150 short calls |
| Azure AI Speech (F0 free tier) | Free up to 0.5 million characters a month. One full match of commentary is about 55,000 |

Delete everything after the hackathon with `az group delete --name rg-touchline`.
