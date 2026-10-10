"""Fill a development database with made-up runs.

    RADIATOR_DATABASE_URL=... python -m radiator.demo

Everything here is fake: it exists to develop and screenshot the dashboard
against something shaped like real history (regressions, fixes, ranks that
drift, a flaky agent, performance sweeps). Never point this at production.
"""

import random
import uuid
from datetime import datetime, timedelta, timezone

import radiator_schema as schema
from radiator.config import Settings  # noqa: F401  (documents the env var)
from radiator.db import make_sessionmaker
from radiator.ingest import ingest_payload

AGENTS = ["ars", "shepherd-aragorn", "shepherd-arax", "shepherd-bte"]

PAIRS = [
    ("Metformin", "type 2 diabetes mellitus", "CHEBI:6801", "MONDO:0005148"),
    ("Imatinib", "chronic myelogenous leukemia", "CHEBI:45783", "MONDO:0011996"),
    ("Albuterol", "asthma", "CHEBI:2549", "MONDO:0004979"),
    ("Levothyroxine", "hypothyroidism", "CHEBI:18332", "MONDO:0005420"),
    ("Sumatriptan", "migraine disorder", "CHEBI:10650", "MONDO:0005277"),
    ("Lisinopril", "hypertension", "CHEBI:43755", "MONDO:0005044"),
    ("Methotrexate", "rheumatoid arthritis", "CHEBI:44185", "MONDO:0008383"),
    ("Donepezil", "Alzheimer disease", "CHEBI:53289", "MONDO:0004975"),
    ("Omeprazole", "gastroesophageal reflux", "CHEBI:7772", "MONDO:0007186"),
    ("Sertraline", "major depressive disorder", "CHEBI:9123", "MONDO:0002009"),
    ("Allopurinol", "gout", "CHEBI:40279", "MONDO:0005393"),
    ("Tamoxifen", "breast carcinoma", "CHEBI:41774", "MONDO:0004989"),
    ("Warfarin", "venous thromboembolism", "CHEBI:10033", "MONDO:0005147"),
    ("Hydroxychloroquine", "lupus erythematosus", "CHEBI:5801", "MONDO:0004670"),
    ("Isoniazid", "tuberculosis", "CHEBI:6030", "MONDO:0018076"),
    ("Riluzole", "amyotrophic lateral sclerosis", "CHEBI:8863", "MONDO:0004976"),
    ("Ivermectin", "onchocerciasis", "CHEBI:6078", "MONDO:0005301"),
    ("Montelukast", "allergic rhinitis", "CHEBI:50730", "MONDO:0011786"),
    ("Pyridostigmine", "myasthenia gravis", "CHEBI:8665", "MONDO:0009688"),
    ("Dapsone", "leprosy", "CHEBI:4325", "MONDO:0005124"),
    ("Insulin glargine", "type 1 diabetes mellitus", "CHEBI:5931", "MONDO:0005147"),
    ("Penicillamine", "Wilson disease", "CHEBI:7959", "MONDO:0010200"),
    ("Ursodiol", "primary biliary cholangitis", "CHEBI:9907", "MONDO:0005388"),
    ("Nitisinone", "tyrosinemia type I", "CHEBI:50378", "MONDO:0010161"),
]
GENES = [
    ("Bortezomib", "PSMB5", "CHEBI:52717", "NCBIGene:5693"),
    ("Erlotinib", "EGFR", "CHEBI:114785", "NCBIGene:1956"),
    ("Simvastatin", "HMGCR", "CHEBI:9150", "NCBIGene:3156"),
    ("Celecoxib", "PTGS2", "CHEBI:41423", "NCBIGene:5743"),
    ("Rapamycin", "MTOR", "CHEBI:9168", "NCBIGene:2475"),
    ("Vemurafenib", "BRAF", "CHEBI:63637", "NCBIGene:673"),
    ("Tofacitinib", "JAK3", "CHEBI:71200", "NCBIGene:3718"),
    ("Olaparib", "PARP1", "CHEBI:83766", "NCBIGene:142"),
]
NEVER = [
    ("Ethanol", "type 2 diabetes mellitus", "CHEBI:16236", "MONDO:0005148"),
    ("Caffeine", "chronic myelogenous leukemia", "CHEBI:27732", "MONDO:0011996"),
    ("Sodium chloride", "hypertension", "CHEBI:26710", "MONDO:0005044"),
    ("Glucose", "migraine disorder", "CHEBI:17234", "MONDO:0005277"),
]


def build_assets():
    assets = []
    expectations = [
        "TopAnswer",
        "TopAnswer",
        "Acceptable",
        "Acceptable",
        "BadButForgivable",
    ]
    for i, (drug, disease, chem, mondo) in enumerate(PAIRS):
        assets.append(
            dict(
                test_case_id=f"TestCase_{i // 4 + 1}",
                asset_id=f"Asset_{i + 1}",
                name=f"{drug} treats {disease}",
                expected_output=expectations[i % 5],
                predicate="biolink:treats",
                input_curie=mondo,
                output_curie=chem,
                difficulty=random.random(),
            )
        )
    for j, (drug, gene, chem, ncbi) in enumerate(GENES):
        assets.append(
            dict(
                test_case_id=f"TestCase_{20 + j // 4}",
                asset_id=f"Asset_{100 + j}",
                name=f"{drug} affects {gene}",
                expected_output="TopAnswer" if j % 3 else "Acceptable",
                predicate="biolink:affects",
                input_curie=ncbi,
                output_curie=chem,
                difficulty=random.random(),
            )
        )
    for k, (chem_name, disease, chem, mondo) in enumerate(NEVER):
        assets.append(
            dict(
                test_case_id="TestCase_30",
                asset_id=f"Asset_{200 + k}",
                name=f"{chem_name} treats {disease}",
                expected_output="NeverShow",
                predicate="biolink:treats",
                input_curie=mondo,
                output_curie=chem,
                difficulty=0.2,
            )
        )
    return assets


AGENT_SKILL = {
    "ars": 0.78,
    "shepherd-aragorn": 0.7,
    "shepherd-arax": 0.66,
    "shepherd-bte": 0.55,
}


def agent_result(agent, spec, run_index, n_runs, rng):
    skill = AGENT_SKILL[agent]
    # aragorn regresses for a stretch mid-month, then a fix lands
    if agent == "shepherd-aragorn" and n_runs * 0.45 < run_index < n_runs * 0.7:
        skill -= 0.25
    # bte gets steadily better
    if agent == "shepherd-bte":
        skill += 0.25 * run_index / n_runs
    roll = rng.random()
    if roll < 0.03:
        return schema.AgentResult(
            agent=agent, status="FAILED", message="Timed out", http_status=598
        )
    if roll < 0.06 and agent != "ars":
        return schema.AgentResult(
            agent=agent, status="ERROR", message="Status code: 500", http_status=500
        )
    n_results = int(rng.lognormvariate(5.2, 0.7))
    if rng.random() < 0.05:
        return schema.AgentResult(
            agent=agent,
            status="NO_RESULTS",
            message="No results",
            http_status=200,
            n_results=0,
            found=False,
        )
    expected = spec["expected_output"]
    good = rng.random() < skill + 0.25 - spec["difficulty"] * 0.4
    if expected == "NeverShow":
        found = not good
        rank = rng.randint(1, n_results) if found else None
        status = "PASSED" if not found else "FAILED"
    else:
        found = good or rng.random() < 0.5
        if found:
            base = 1 + spec["difficulty"] * 40
            rank = max(1, int(rng.gauss(base if good else base * 4 + 30, 5)))
            rank = min(rank, n_results)
        else:
            rank = None
        limit = {
            "TopAnswer": 30,
            "Acceptable": n_results // 2,
            "BadButForgivable": n_results,
        }[expected]
        status = "PASSED" if found and rank is not None and rank <= limit else "FAILED"
    score = (
        None
        if rank is None
        else round(max(0.05, 1 - rank / max(n_results, 1)) * rng.uniform(0.85, 1.0), 3)
    )
    return schema.AgentResult(
        agent=agent,
        status=status,
        http_status=200,
        found=found,
        rank=rank,
        score=score,
        n_results=n_results,
        pk=str(uuid.UUID(int=rng.getrandbits(128))),
    )


def demo_runs(days=30, env="ci", suite="sprint_4_tests", seed=7):
    rng = random.Random(seed)
    random.seed(seed)
    assets = build_assets()
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    n_runs = days
    payloads = []

    def acceptance(env, key, started, i, skip_all=False):
        run = schema.RunCreate(
            run_id=uuid.uuid5(uuid.NAMESPACE_URL, f"demo:{suite}:{env}:{key}"),
            suite=suite,
            env=env,
            harness_version="0.8.0",
            tests_source="https://github.com/NCATSTranslator/Tests/archive/refs/heads/main.zip",
            started_at=started,
        )
        results = []
        counts = {}
        for spec in assets:
            if skip_all:
                agents = [schema.AgentResult(agent=a, status="SKIPPED") for a in AGENTS]
            else:
                agents = [agent_result(a, spec, i, n_runs, rng) for a in AGENTS]
            overall = agents[0].status
            counts[overall] = counts.get(overall, 0) + 1
            fields = {k: v for k, v in spec.items() if k != "difficulty"}
            results.append(
                schema.AssetResult(
                    kind="acceptance",
                    status=overall,
                    agents=agents,
                    parent_pk=str(uuid.UUID(int=rng.getrandbits(128))),
                    **fields,
                )
            )
        payloads.append(
            schema.RunPayload(
                run=run,
                results=results,
                finish=schema.RunFinish(
                    ended_at=started + timedelta(minutes=rng.randint(38, 75)),
                    counts=counts,
                ),
            )
        )

    # ci: daily
    for i in range(n_runs):
        started = (
            now
            - timedelta(days=n_runs - 1 - i, hours=-6 if i == n_runs - 1 else 0)
            - timedelta(hours=8)
        )
        acceptance(env, i, started, i)
    # test: every few days; prod: weekly, with a quick re-run now and then
    for i in range(0, n_runs, 3):
        acceptance("test", i, now - timedelta(days=n_runs - 1 - i, hours=3), i)
    for i in range(2, n_runs, 7):
        acceptance("prod", i, now - timedelta(days=n_runs - 1 - i, hours=-2), i)
        if i % 2:
            acceptance(
                "prod", f"{i}b", now - timedelta(days=n_runs - 1 - i, hours=-12), i
            )
    # dev: every four days, and often broken (everything skipped)
    for i in range(1, n_runs, 4):
        acceptance(
            "dev",
            i,
            now - timedelta(days=n_runs - 1 - i, hours=10),
            i,
            skip_all=i % 8 == 1,
        )

    # performance sweeps every few days: like test-harness-sweep, one run per
    # service, in turn, each with that service as its target
    hosts = [
        ("https://ars.ci.transltr.io", "ars", 14.0),
        ("https://aragorn.ci.transltr.io", "aragorn", 9.0),
        ("https://arax.ci.transltr.io", "arax", 6.5),
        ("https://bte.ci.transltr.io", "bte", 11.0),
    ]
    for sweep in range(days // 3 + 1):
        sweep_start = now - timedelta(days=3 * sweep, hours=30)
        for position, (host, target, base) in enumerate(hosts):
            started = sweep_start + timedelta(minutes=70 * position)
            # a slow decline for arax, a step up for bte a week and a half ago
            trend = -0.15 * (days // 3 - sweep) if target == "arax" else 0
            if target == "bte" and sweep < 4:
                trend = 2.5
            msc = round(max(1.0, rng.gauss(base - trend, 0.9)), 1)
            passed = msc >= base * 0.8
            run = schema.RunCreate(
                run_id=uuid.uuid5(uuid.NAMESPACE_URL, f"demo:perf:{sweep}:{target}"),
                suite="performance_tests",
                env=env,
                target=target,
                target_url=host,
                harness_version="0.8.0",
                started_at=started,
            )
            perf = schema.PerformanceResult(
                test_case_id="Perf_1",
                asset_id="Perf_mixed",
                host=host,
                helmsdeep_target=target,
                profile="mixed",
                status="PASSED" if passed else "FAILED",
                exit_code=0,
                max_sustainable_concurrency=msc,
                checkpoints_passed=passed,
                summary={
                    "max_sustainable_concurrency": msc,
                    "checkpoints_passed": passed,
                },
            )
            payloads.append(
                schema.RunPayload(
                    run=run,
                    performance=[perf],
                    finish=schema.RunFinish(ended_at=started + timedelta(minutes=65)),
                )
            )
    return payloads


def main():
    import os

    sessionmaker = make_sessionmaker(os.environ["RADIATOR_DATABASE_URL"])
    # in the order they ran, so their run numbers follow time
    payloads = sorted(demo_runs(), key=lambda p: p.run.started_at)
    with sessionmaker() as session:
        for payload in payloads:
            ingest_payload(session, payload)
        session.commit()
    print(f"Loaded {len(payloads)} demo runs.")


if __name__ == "__main__":
    main()
