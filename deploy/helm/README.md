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

The secrets (the radiator's login, API token and database password, the
harness's Slack and Zebrunner tokens) go under `secrets` in those copies, and
each chart keeps them in a Secret of its own: `<release>-secrets`, plus
`<release>-postgres` for the radiator's database password. That makes the
copies the only place the real values live, so keep them somewhere safe (a
password manager, or an encrypted backup), never in git.

Two values must agree across the charts: the harness's `secrets.radiatorToken`
is the radiator's `secrets.apiToken`.

### Moving from Secrets you created yourself

Earlier versions of the charts could use Secrets made with `kubectl create
secret` (`existingSecret`). With the default release names, those have the
same names the charts now create (`radiator-secrets`, `radiator-postgres`,
`test-harness-secrets`), so Helm refuses to install over them until it's told
they're its own:

```
for s in radiator-secrets radiator-postgres; do
  kubectl -n translator annotate secret $s meta.helm.sh/release-name=radiator meta.helm.sh/release-namespace=translator
  kubectl -n translator label secret $s app.kubernetes.io/managed-by=Helm
done
kubectl -n translator annotate secret test-harness-secrets meta.helm.sh/release-name=test-harness meta.helm.sh/release-namespace=translator
kubectl -n translator label secret test-harness-secrets app.kubernetes.io/managed-by=Helm
```

Before upgrading, copy their current values into your values copies, since
the upgrade overwrites them with what's there. Above all, `postgres.password`
must be the database's existing password: Postgres set it when its volume was
first created and won't take a new one from the Secret.

```
kubectl -n translator get secret radiator-secrets -o json | jq '.data | map_values(@base64d)'
```

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
The harness's `secrets.radiatorToken` is the radiator's `secrets.apiToken`.

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

Once the radiator is up, set `zebrunnerImport.enabled: true` with its
`baseUrl` and `refreshToken` (try
`args: ["--dry-run", "--limit", "5"]` first) and upgrade; the Job runs once per
upgrade while it's enabled. Turn it off again afterwards. See
[Importing from Zebrunner](../../docs/information-radiator.md#importing-from-zebrunner).

## Checking a change

```
helm lint deploy/helm/radiator -f deploy/helm/radiator/values.example.yaml
helm template radiator deploy/helm/radiator -f deploy/helm/radiator/values.example.yaml
```

CI lints and renders both charts with their example values.
