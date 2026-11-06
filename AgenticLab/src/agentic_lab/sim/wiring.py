"""Builds the Simulator for the CLI and the Simulation tab: folder releaser, webhook trigger, runs client."""
from agentic_lab.sim.release import FolderReleaser
from agentic_lab.sim.settings import load_sim_settings
from agentic_lab.sim.simulator import Simulator
from agentic_lab.sim.trigger import RunsClient, WebhookTrigger


def build_simulator(versions: list[str]) -> Simulator:
    """The Simulator for `versions`, wired to the folder releaser and the agentic service. It replays the course
    folders in SIM_DATA_DIR; DATA_DIR is what the agentic service reads and plays no part here."""
    sim = load_sim_settings()
    return Simulator(sim.sim_data_dir, list(versions), FolderReleaser(sim.sim_data_dir, sim.sim_dir),
                     WebhookTrigger(sim.agentic_url, sim.webhook_secret),
                     RunsClient(sim.agentic_url, sim.agentic_api_token), sim.sim_dir,
                     concurrency=sim.concurrency)
