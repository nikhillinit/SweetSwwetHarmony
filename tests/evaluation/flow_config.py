"""
Inspect Flow Configuration for Thesis Classification Parameter Sweep

Defines the parameter matrix for evaluating thesis classification across:
- Multiple models (Gemini, GPT-4o variants)
- Multiple solver strategies (basic, CoT, self-critique)
- Multiple temperature settings

Usage:
    # Preview sweep configuration
    flow run tests/evaluation/flow_config.py --dry-run

    # Run full parameter sweep
    flow run tests/evaluation/flow_config.py

    # View results
    inspect view logs/thesis_eval
"""

from inspect_flow import FlowSpec, FlowTask, tasks_matrix, models_matrix, configs_matrix


# =============================================================================
# FLOW SPECIFICATION
# =============================================================================

# Full parameter sweep across models and solvers
flow_spec = FlowSpec(
    log_dir="logs/thesis_eval",
    tasks=tasks_matrix(
        task=[
            # Different solver strategies
            "tests/evaluation/thesis_eval:thesis_classification",
            "tests/evaluation/thesis_eval:thesis_with_cot",
            "tests/evaluation/thesis_eval:thesis_with_self_critique",
            "tests/evaluation/thesis_eval:thesis_verbose_cot",
        ],
        model=models_matrix(
            model=[
                # Google models (free tier available)
                "google/gemini-1.5-flash",
                "google/gemini-1.5-pro",
                "google/gemini-2.0-flash",

                # OpenAI models
                "openai/gpt-4o-mini",
                "openai/gpt-4o",
            ],
            config=configs_matrix(
                temperature=[0.0, 0.3],  # Low variance for classification
            ),
        ),
    ),
)


# =============================================================================
# QUICK SWEEP (for development/testing)
# =============================================================================

quick_sweep = FlowSpec(
    log_dir="logs/thesis_eval_quick",
    tasks=tasks_matrix(
        task=[
            "tests/evaluation/thesis_eval:thesis_classification",
            "tests/evaluation/thesis_eval:thesis_with_cot",
        ],
        model=models_matrix(
            model=[
                "google/gemini-1.5-flash",  # Fast and free
                "openai/gpt-4o-mini",       # Fast and cheap
            ],
            config=configs_matrix(
                temperature=[0.0],
            ),
        ),
    ),
)


# =============================================================================
# MODEL-SPECIFIC SWEEPS
# =============================================================================

gemini_sweep = FlowSpec(
    log_dir="logs/thesis_eval_gemini",
    tasks=tasks_matrix(
        task=[
            "tests/evaluation/thesis_eval:thesis_classification",
            "tests/evaluation/thesis_eval:thesis_with_cot",
            "tests/evaluation/thesis_eval:thesis_with_self_critique",
        ],
        model=models_matrix(
            model=[
                "google/gemini-1.5-flash",
                "google/gemini-1.5-pro",
                "google/gemini-2.0-flash",
            ],
            config=configs_matrix(
                temperature=[0.0, 0.2, 0.5],
            ),
        ),
    ),
)


openai_sweep = FlowSpec(
    log_dir="logs/thesis_eval_openai",
    tasks=tasks_matrix(
        task=[
            "tests/evaluation/thesis_eval:thesis_classification",
            "tests/evaluation/thesis_eval:thesis_with_cot",
            "tests/evaluation/thesis_eval:thesis_with_self_critique",
        ],
        model=models_matrix(
            model=[
                "openai/gpt-4o-mini",
                "openai/gpt-4o",
            ],
            config=configs_matrix(
                temperature=[0.0, 0.3],
            ),
        ),
    ),
)


# =============================================================================
# SOLVER COMPARISON (single model, all solvers)
# =============================================================================

solver_comparison = FlowSpec(
    log_dir="logs/thesis_eval_solvers",
    tasks=tasks_matrix(
        task=[
            "tests/evaluation/thesis_eval:thesis_classification",
            "tests/evaluation/thesis_eval:thesis_with_cot",
            "tests/evaluation/thesis_eval:thesis_with_self_critique",
            "tests/evaluation/thesis_eval:thesis_verbose_cot",
        ],
        model=models_matrix(
            model=["google/gemini-2.0-flash"],
            config=configs_matrix(
                temperature=[0.0],
            ),
        ),
    ),
)


# =============================================================================
# HELPERS
# =============================================================================

def list_specs() -> dict:
    """List all available flow specifications."""
    return {
        "flow_spec": "Full parameter sweep (5 models × 4 solvers × 2 temps)",
        "quick_sweep": "Quick test sweep (2 models × 2 solvers)",
        "gemini_sweep": "Gemini-only sweep (3 models × 3 solvers × 3 temps)",
        "openai_sweep": "OpenAI-only sweep (2 models × 3 solvers × 2 temps)",
        "solver_comparison": "Solver comparison (1 model × 4 solvers)",
    }


if __name__ == "__main__":
    print("Available flow specifications:")
    for name, desc in list_specs().items():
        print(f"  {name}: {desc}")

    print("\nUsage:")
    print("  flow run tests/evaluation/flow_config.py")
    print("  flow run tests/evaluation/flow_config.py:quick_sweep")
    print("  flow run tests/evaluation/flow_config.py:gemini_sweep --dry-run")
