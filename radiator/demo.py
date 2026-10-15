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


def agent_result(agent, spec, run_index, n_runs, rng, skill_offset=0.0):
    skill = AGENT_SKILL[agent] + skill_offset
    # aragorn regresses for a stretch, then a fix lands
    if agent == "shepherd-aragorn" and n_runs * 0.45 < run_index < n_runs * 0.7:
        skill -= 0.25
    # bte gets steadily better
    if agent == "shepherd-bte":
        skill += 0.25 * run_index / n_runs
    # the shepherds are queried directly, so their response times are measured
    response_time_s = (
        None if agent == "ars" else round(rng.lognormvariate(1.6, 0.35), 2)
    )
    roll = rng.random()
    if roll < 0.03:
        return schema.AgentResult(
            agent=agent, status="FAILED", message="Timed out", http_status=598
        )
    if roll < 0.06 and agent != "ars":
        return schema.AgentResult(
            agent=agent,
            status="ERROR",
            message="Status code: 500",
            http_status=500,
            response_time_s=response_time_s,
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
            response_time_s=response_time_s,
        )
    expected = spec["expected_output"]
    good = rng.random() < skill + 0.25 - spec["difficulty"] * 0.4
    if expected is None:  # pathfinder: did the expected path come back?
        status = "PASSED" if good else "FAILED"
        return schema.AgentResult(
            agent=agent,
            status=status,
            http_status=200,
            found=good,
            n_results=n_results,
            response_time_s=response_time_s,
            expected_nodes_found="3/3" if good else f"{rng.randint(0, 2)}/3",
            pk=str(uuid.UUID(int=rng.getrandbits(128))),
        )
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
        response_time_s=response_time_s,
        pk=str(uuid.UUID(int=rng.getrandbits(128))),
    )


PATHS = [
    ("Imatinib", "chronic myelogenous leukemia", "CHEBI:45783", "MONDO:0011996"),
    ("Metformin", "polycystic ovary syndrome", "CHEBI:6801", "MONDO:0008487"),
    ("Sildenafil", "pulmonary hypertension", "CHEBI:9139", "MONDO:0005149"),
    ("Thalidomide", "multiple myeloma", "CHEBI:9513", "MONDO:0009693"),
    ("Aspirin", "colorectal cancer", "CHEBI:15365", "MONDO:0005575"),
    ("Propranolol", "infantile hemangioma", "CHEBI:8499", "MONDO:0006500"),
    ("Rituximab", "pemphigus vulgaris", "CHEBI:64357", "MONDO:0006570"),
    ("Dexamethasone", "COVID-19", "CHEBI:41879", "MONDO:0100096"),
    ("Baricitinib", "alopecia areata", "CHEBI:95341", "MONDO:0005343"),
    ("Tocilizumab", "giant cell arteritis", "CHEBI:64359", "MONDO:0008798"),
    ("Valproic acid", "bipolar disorder", "CHEBI:39867", "MONDO:0004985"),
    ("Topiramate", "obesity", "CHEBI:63631", "MONDO:0011122"),
]


def build_paths():
    return [
        dict(
            test_case_id=f"PathCase_{i // 4 + 1}",
            asset_id=f"Path_{i + 1}",
            name=f"{drug} to {disease}",
            expected_output=None,
            input_curie=chem,
            output_curie=mondo,
            difficulty=random.random() * 0.8,
        )
        for i, (drug, disease, chem, mondo) in enumerate(PATHS)
    ]


# How each environment differs: dev is the roughest, prod the steadiest.
ENV_SKILL = {"dev": -0.1, "ci": 0.0, "test": 0.02, "prod": 0.05}
# The rotation: one environment a day, Monday to Thursday.
ENV_DAY = {"dev": 0, "ci": 1, "test": 2, "prod": 3}
# The share of agent results that come out differently from last week.
CHURN = 0.06
ARS_CONCURRENCY = {"dev": 9.0, "ci": 14.0, "test": 13.0, "prod": 16.0}


def ars_url(env):
    return (
        "https://ars.transltr.io" if env == "prod" else f"https://ars.{env}.transltr.io"
    )


def demo_runs(weeks=8, seed=7):
    """Weekly run cycles in each environment (acceptance, pathfinder, ARS
    performance), plus a Sunday performance sweep of the ARAs on ci.

    The latest week carries a few oddities for the flags to find: arax stops
    returning results on some ci assets and bte gets slower there, aragorn
    ranks a couple of ci answers much lower while still passing them, dev's
    aragorn starts erroring, and the ARS on test loses a third of its
    concurrency.
    """
    rng = random.Random(seed)
    random.seed(seed)
    assets = build_assets()
    paths = build_paths()
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    this_monday = (now - timedelta(days=now.weekday())).replace(hour=6)
    first_monday = this_monday - timedelta(weeks=weeks - 1)
    payloads = []

    def results_run(env, kind, suite, key, started, week, cycle_id, quirks=()):
        run = schema.RunCreate(
            run_id=uuid.uuid5(uuid.NAMESPACE_URL, f"demo:{suite}:{env}:{key}"),
            suite=suite,
            env=env,
            harness_version="0.8.0",
            tests_source="https://github.com/NCATSTranslator/Tests/archive/refs/heads/main.zip",
            started_at=started,
            cycle_id=cycle_id,
        )
        latest = week == weeks - 1
        results = []
        counts = {}
        specs = assets if kind == "acceptance" else paths
        for n, spec in enumerate(specs):
            if "skip_all" in quirks:
                agents = [schema.AgentResult(agent=a, status="SKIPPED") for a in AGENTS]
            else:
                agents = []
                for a in AGENTS:
                    # Mostly the same answer as last week: an agent's outcome
                    # on an asset is seeded by the asset, and only now and
                    # then (or as its skill drifts) does it change.
                    stable = random.Random(f"{seed}:{env}:{a}:{spec['asset_id']}")
                    pick = rng if rng.random() < CHURN else stable
                    agents.append(
                        agent_result(a, spec, week, weeks, pick, ENV_SKILL[env])
                    )
            for i, agent in enumerate(agents):
                if "arax_no_results" in quirks and agent.agent == "shepherd-arax":
                    if latest and n < 5 and agent.status != "SKIPPED":
                        agents[i] = schema.AgentResult(
                            agent=agent.agent,
                            status="NO_RESULTS",
                            message="No results",
                            http_status=200,
                            n_results=0,
                            found=False,
                            response_time_s=agent.response_time_s,
                        )
                    elif n < 5:
                        agents[i] = agent.model_copy(
                            update={
                                "status": schema.Status.PASSED,
                                "n_results": 180,
                                "found": True,
                            }
                        )
                if "bte_slower" in quirks and agent.agent == "shepherd-bte":
                    if agent.response_time_s is not None:
                        agents[i] = agent.model_copy(
                            update={"response_time_s": agent.response_time_s * 4 + 6}
                        )
                if "aragorn_rank" in quirks and agent.agent == "shepherd-aragorn":
                    if spec["expected_output"] == "BadButForgivable" and n < 12:
                        agents[i] = agent.model_copy(
                            update={
                                "status": schema.Status.PASSED,
                                "found": True,
                                "rank": (52 + n) if latest else 4,
                                "n_results": 400,
                                "score": 0.2 if latest else 0.9,
                            }
                        )
                if "aragorn_errors" in quirks and agent.agent == "shepherd-aragorn":
                    if latest and n % 5 == 0:
                        agents[i] = schema.AgentResult(
                            agent=agent.agent,
                            status="ERROR",
                            message="Status code: 502",
                            http_status=502,
                        )
                    elif not latest and agent.status == "ERROR":
                        agents[i] = agent.model_copy(
                            update={"status": schema.Status.FAILED, "http_status": 200}
                        )
            overall = agents[0].status
            counts[overall] = counts.get(overall, 0) + 1
            fields = {k: v for k, v in spec.items() if k != "difficulty"}
            results.append(
                schema.AssetResult(
                    kind=kind,
                    status=overall,
                    agents=agents,
                    parent_pk=str(uuid.UUID(int=rng.getrandbits(128))),
                    **fields,
                )
            )
        minutes = rng.randint(42, 58) if kind == "acceptance" else rng.randint(8, 15)
        payloads.append(
            schema.RunPayload(
                run=run,
                results=results,
                finish=schema.RunFinish(
                    ended_at=started + timedelta(minutes=minutes), counts=counts
                ),
            )
        )
        return run.started_at + timedelta(minutes=minutes + 2)

    def performance_run(env, key, started, host, target, msc, cycle_id=None):
        passed = (
            msc >= ARS_CONCURRENCY.get(env, 10) * 0.8 if target == "ars" else msc >= 5
        )
        run = schema.RunCreate(
            run_id=uuid.uuid5(uuid.NAMESPACE_URL, f"demo:perf:{env}:{key}:{target}"),
            suite="performance_tests",
            env=env,
            target=target,
            target_url=host,
            harness_version="0.8.0",
            started_at=started,
            cycle_id=cycle_id,
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
            summary={"max_sustainable_concurrency": msc, "checkpoints_passed": passed},
        )
        payloads.append(
            schema.RunPayload(
                run=run,
                performance=[perf],
                finish=schema.RunFinish(ended_at=started + timedelta(minutes=68)),
            )
        )

    quirks = {
        "ci": ("arax_no_results", "bte_slower", "aragorn_rank"),
        "dev": ("aragorn_errors",),
    }
    for week in range(weeks):
        for env, day in ENV_DAY.items():
            started = first_monday + timedelta(weeks=week, days=day)
            if started > now:
                continue
            cycle_id = uuid.uuid5(uuid.NAMESPACE_URL, f"demo:cycle:{env}:{week}")
            env_quirks = quirks.get(env, ())
            # one dev week where nothing could run
            if env == "dev" and week == weeks - 3:
                env_quirks = ("skip_all",)
            # bte is only slower this week
            if "bte_slower" in env_quirks and week != weeks - 1:
                env_quirks = tuple(q for q in env_quirks if q != "bte_slower")
            t = results_run(
                env,
                "acceptance",
                "sprint_4_tests",
                week,
                started,
                week,
                cycle_id,
                env_quirks,
            )
            t = results_run(
                env, "pathfinder", "pathfinder_tests", week, t, week, cycle_id
            )
            msc = rng.gauss(ARS_CONCURRENCY[env], 0.5)
            if env == "test" and week == weeks - 1:
                msc *= 0.66
            performance_run(
                env, week, t, ars_url(env), "ars", round(max(1.0, msc), 1), cycle_id
            )

    # a Sunday sweep of the ARAs on ci, outside the cycles
    for week in range(weeks):
        sweep_start = first_monday + timedelta(weeks=week, days=-1, hours=-4)
        for position, (target, base) in enumerate(
            [("aragorn", 9.0), ("arax", 6.5), ("bte", 11.0)]
        ):
            trend = -0.3 * week if target == "arax" else 0
            msc = round(max(1.0, rng.gauss(base + trend, 0.6)), 1)
            performance_run(
                "ci",
                f"sweep{week}",
                sweep_start + timedelta(minutes=70 * position),
                f"https://{target}.ci.transltr.io",
                target,
                msc,
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
