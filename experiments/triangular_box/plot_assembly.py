"""Plot measured simulation trajectories, not renders or hardware experiments."""

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    fig, axes = plt.subplots(1, 3, figsize=(15, 4), layout='constrained')
    for label, name in [('0 → 1 → 2', 'full-012'), ('2 → 1 → 0', 'full-210')]:
        report = json.loads((args.root / 'round-03' / name / 'result.json').read_text())
        probes = report['probes']
        axes[0].plot([p['removed_count'] for p in probes],
                     [min(v[2] for v in p['geometry']['anchor_displacement_body_m']) * 1000
                      for p in probes], marker='o', label=label)
        rows = [r for r in report['trace'] if r['t'] >= 2]
        axes[1].plot([r['t'] for r in rows],
                     [max((abs(s['helix_residual_m']) * 1e6 for s in r['screws']
                           if s['thread_active']), default=float('nan')) for r in rows],
                     label=label, linewidth=1.)
    axes[0].axhline(20, linestyle='--', color='gray', label='Required after all removed')
    axes[0].set(xlabel='Removed screws', ylabel='Minimum lid-anchor lift (mm)',
                title='Fixed base: final pull after each removal', xticks=[0, 1, 2, 3])
    axes[1].axhline(50, linestyle='--', color='gray', label='50 µm acceptance limit')
    axes[1].set(xlabel='Simulation time (s)', ylabel='Max active helix residual (µm)',
                title='Fixed base: constraint transients', ylim=(0, 145))
    for label, name in [('1 ms: complete run', 'round-02/one-nominal'),
                        ('0.5 ms: first 10 s only', 'round-06/nominal-halfstep-diagnostic')]:
        report = json.loads((args.root / name / 'result.json').read_text())
        rows = [r for r in report['trace'] if 7.2 <= r['t'] <= 8.2]
        axes[2].plot([r['t'] for r in rows],
                     [abs(r['screws'][0]['helix_residual_m']) * 1e6 for r in rows],
                     marker='.', label=label, linewidth=1.)
    axes[2].axhline(50, linestyle='--', color='gray')
    axes[2].set(xlabel='Simulation time (s)', ylabel='Screw 0 helix residual (µm)',
                title='30 rpm: timestep diagnostic', ylim=(0, 125))
    for ax in axes:
        ax.grid(alpha=.25)
        ax.legend(fontsize=8)
    fig.suptitle('Isaac Sim ideal-carriage bench — recorded simulation data; no robot or hardware')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        raise FileExistsError(args.output)
    fig.savefig(args.output, dpi=160)
    plt.close(fig)


if __name__ == '__main__':
    main()
