const API_VERSION = "2026-03-10";

export function githubSchedule(cron) {
  return String(cron || "").replace(/MON-FRI/gi, "1-5");
}

export async function dispatchWorkflow(env, cron, fetchImpl = fetch) {
  if (!env.GITHUB_ACTIONS_TOKEN) throw new Error("GITHUB_ACTIONS_TOKEN is not configured");

  const owner = env.GITHUB_OWNER || "L1997x";
  const repo = env.GITHUB_REPO || "A-share-LI";
  const workflow = env.GITHUB_WORKFLOW || "update-data.yml";
  const ref = env.GITHUB_REF || "main";
  const url = `https://api.github.com/repos/${owner}/${repo}/actions/workflows/${workflow}/dispatches`;
  const response = await fetchImpl(url, {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${env.GITHUB_ACTIONS_TOKEN}`,
      "Content-Type": "application/json",
      "User-Agent": "a-share-li-cloudflare-refresh",
      "X-GitHub-Api-Version": API_VERSION,
    },
    body: JSON.stringify({
      ref,
      inputs: {
        trigger_source: "cloudflare",
        target_schedule: githubSchedule(cron),
      },
    }),
  });

  if (!response.ok) {
    const detail = (await response.text()).slice(0, 500);
    throw new Error(`GitHub workflow dispatch failed: HTTP ${response.status} ${detail}`);
  }

  return { status: response.status, cron };
}

export default {
  scheduled(controller, env, context) {
    context.waitUntil(
      dispatchWorkflow(env, controller.cron)
        .then((result) => console.log(JSON.stringify({ event: "github_dispatch", outcome: "ok", ...result })))
        .catch((error) => {
          console.error(JSON.stringify({ event: "github_dispatch", outcome: "error", cron: controller.cron, message: error.message }));
          throw error;
        })
    );
  },
};
