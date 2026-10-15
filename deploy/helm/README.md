# Helm charts

Two charts, installed into the same namespace:

| Chart | What it deploys |
|---|---|
| [`radiator`](radiator) | The Information Radiator: the web app (with a migration init container), its Service, an optional Ingress, an optional small Postgres, and an optional one-off Zebrunner import Job |
| [`test-harness`](test-harness) | The scheduled runs: a weekly run cycle per environment, the Monday digest, and an optional performance sweep (eg of the ARAs) |

## Values

Each chart's `values.yaml` holds the defaults; don't edit it for a deployment.
Copy `values.example.yaml` to `values.<name>.yaml` in the same directory and
fill that in. Those copies are git-ignored (only `values.example.yaml` is
tracked), so `values.prod.yaml` or `values.ci.yaml` never end up in a commit.

```
cp deploy/helm/radiator/values.example.yaml deploy/helm/radiator/values.prod.yaml
cp deploy/helm/test-harness/values.example.yaml deploy/helm/test-harness/values.prod.yaml
```

Secrets can go in two ways. The examples use the first:

- **Create the Secrets yourself** and name them in `existingSecret` (and
  `postgres.existingSecret`). The `kubectl create secret` commands are in each
  example file. Nothing secret is then in any values file.
- **Let the chart create them**: leave `existingSecret` empty and fill in
  `secrets`. The copy is git-ignored, but it's still a file with passwords in
  it, so keep it somewhere safe.

## Installing

The radiator first, since the harness reports to it:

```
kubectl create namespace translator   # if needed

helm upgrade --install radiator deploy/helm/radiator \
  -n translator -f deploy/helm/radiator/values.prod.yaml

helm upgrade --install test-harness deploy/helm/test-harness \
  -n translator -f deploy/helm/test-harness/values.prod.yaml
```

With a release named `radiator`, the radiator's Service is `radiator`, which is
what the harness example's `radiator.url` (`http://radiator`) expects; with
another release name, use the in-cluster URL the radiator's install notes print.
The harness's `radiator-token` is the radiator's `api-token`.

Upgrades are the same command. The radiator migrates its database before it
starts, every time; that's a no-op when the schema is current.

## The schedule

`cycles.environments` in the harness values is one CronJob per environment. The
example runs dev on Monday, ci on Tuesday, test on Wednesday and prod on
Thursday, each at 01:52 in `timeZone`, and the digest on Monday at 08:52. The
environment comes from the test cases, so each environment names the suites
written for it. Any setting under `cycles.defaults` can be overridden per
environment; `performance: ""` leaves the performance step out (eg for prod).

To run one now, without waiting for its day:

```
kubectl -n translator create job --from=cronjob/test-harness-cycle-ci cycle-ci-now
kubectl -n translator create job --from=cronjob/test-harness-weekly-digest digest-now
```

`suspend: true` on an environment (or `digest.suspend`, `sweep.suspend`) keeps
its CronJob but stops it from running.

## Importing Zebrunner's history

Once the radiator is up, set `zebrunnerImport.enabled: true` (try
`args: ["--dry-run", "--limit", "5"]` first) and upgrade; the Job runs once per
upgrade while it's enabled. Turn it off again afterwards. See
[Importing from Zebrunner](../../docs/information-radiator.md#importing-from-zebrunner).

## Checking a change

```
helm lint deploy/helm/radiator -f deploy/helm/radiator/values.example.yaml
helm template radiator deploy/helm/radiator -f deploy/helm/radiator/values.example.yaml
```

CI lints and renders both charts with their example values.
