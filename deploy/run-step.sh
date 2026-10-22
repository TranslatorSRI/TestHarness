#!/usr/bin/env bash
# Run one step of an environment's cycle now, instead of the whole cycle.
#
#   deploy/run-step.sh ci acceptance
#   deploy/run-step.sh prod performance -n translator
#   deploy/run-step.sh test pathfinder --dry-run      # print the Job, don't create it
#
# The Job is copied from the environment's cycle CronJob, so it has the same
# image, secrets, Slack and radiator settings, resources and deadline; only the
# arguments change, to that step alone with the CronJob's own suite (and, for
# performance, its targets and profile). The step reports as usual and the
# cycle posts its board for the one step.
#
# A Job created by hand ignores the CronJob's concurrencyPolicy, so this
# refuses to start while a Job of the same CronJob is still running (--force
# to start anyway).
#
# Needs kubectl and jq.

set -euo pipefail

usage() {
  cat <<EOF
Usage: $(basename "$0") ENVIRONMENT STEP [options]

  ENVIRONMENT   a cycles.environments name, eg dev, ci, test, prod
  STEP          acceptance, pathfinder or performance

Options:
  -n, --namespace NS   the namespace (default: \$NAMESPACE, else translator)
  --cronjob NAME       the CronJob to copy (default: test-harness-cycle-ENVIRONMENT)
  --name NAME          the Job's name (default: STEP-ENVIRONMENT-<timestamp>)
  --dry-run            print the Job instead of creating it
  --force              start even if a Job of the same CronJob is running
  -h, --help           show this help
EOF
}

die() {
  echo "$(basename "$0"): $*" >&2
  exit 1
}

namespace="${NAMESPACE:-translator-dev}"
cronjob=""
job_name=""
dry_run=false
force=false
positional=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    -n | --namespace) namespace="${2:?$1 needs a value}"; shift 2 ;;
    --cronjob) cronjob="${2:?$1 needs a value}"; shift 2 ;;
    --name) job_name="${2:?$1 needs a value}"; shift 2 ;;
    --dry-run) dry_run=true; shift ;;
    --force) force=true; shift ;;
    -h | --help) usage; exit 0 ;;
    -*) usage >&2; die "unknown option $1" ;;
    *) positional+=("$1"); shift ;;
  esac
done

[[ ${#positional[@]} -eq 2 ]] || { usage >&2; exit 2; }
env_name="${positional[0]}"
step="${positional[1]}"
case "$step" in
  acceptance | pathfinder | performance) ;;
  *) die "STEP must be acceptance, pathfinder or performance, not '$step'" ;;
esac

for tool in kubectl jq; do
  command -v "$tool" >/dev/null || die "needs $tool"
done

cronjob="${cronjob:-test-harness-cycle-$env_name}"
job_name="${job_name:-$step-$env_name-$(date +%Y%m%d-%H%M%S)}"
kc=(kubectl -n "$namespace")

"${kc[@]}" get cronjob "$cronjob" >/dev/null ||
  die "no CronJob $cronjob in namespace $namespace (see --cronjob, -n)"

if ! $dry_run && ! $force; then
  running=$("${kc[@]}" get jobs -o json | jq -r --arg cj "$cronjob" '
    .items[]
    | select(any(.metadata.ownerReferences[]?; .kind == "CronJob" and .name == $cj))
    | select((.status.active // 0) > 0)
    | .metadata.name')
  if [[ -n "$running" ]]; then
    die "a Job of $cronjob is still running ($(echo "$running" | paste -sd, -)):" \
      "two runs at once would load the same services together. Wait for it," \
      "or pass --force."
  fi
fi

# The cycle's arguments with every other step left out: the suite source and
# output flags, this step's flag (with, for performance, its targets and
# profile), and everything after the `--` as it is.
job=$("${kc[@]}" create job "$job_name" --from="cronjob/$cronjob" --dry-run=client -o json |
  jq --arg step "$step" --arg env "$env_name" '
    def keep($flag):
      ["--download", "--tests_url", "--tests_dir", "--output_dir", "--\($step)"]
      + (if $step == "performance"
         then ["--performance_target", "--performance_profile"] else [] end)
      | index([$flag]) != null;
    def only_step:
      (index("--") // length) as $sep
      | (.[:$sep] | reduce .[] as $arg ({out: [], value: null};
          if .value != null then
            # the value of the flag before it
            (if .value then .out += [$arg] else . end) | .value = null
          else
            ($arg | split("=")[0]) as $flag
            | (keep($flag)) as $k
            | (if $k then .out += [$arg] else . end)
            # --download takes no value; --flag=value carries its own
            | if $flag == "--download" or ($arg | contains("=")) then .
              else .value = $k end
          end
        ) | .out) + .[$sep:];
    .spec.template.spec.containers[0].args |= only_step
    | .metadata.labels["translator.ncats.io/environment"] = $env
    | .metadata.labels["translator.ncats.io/step"] = $step
  ')

args=$(jq -c '.spec.template.spec.containers[0].args' <<<"$job")
jq -e --arg flag "--$step" 'index([$flag]) != null or any(.[]; startswith($flag + "="))' \
  <<<"$args" >/dev/null ||
  die "$cronjob has no $step step (eg performance: \"\" in its values)"

if $dry_run; then
  echo "$job"
  exit 0
fi

echo "Starting $step on $env_name: job/$job_name"
echo "  test-harness-cycle $(jq -r 'join(" ")' <<<"$args")"
"${kc[@]}" apply -f - <<<"$job" >/dev/null
echo "Follow it with:"
echo "  kubectl -n $namespace logs -f job/$job_name"
