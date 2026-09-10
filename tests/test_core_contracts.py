import ast
import unittest

from rdl_core import (
    AuthorityConstraint,
    BoundaryContext,
    CommitmentOrigin,
    CommitmentRecord,
    EvidencePolarity,
    Provenance,
    ConstraintActivation,
    ConstraintEvaluationWeights,
    ConstraintEvaluation,
    ConstraintEvaluationComparison,
    ConstraintEvaluationDelta,
    ConstraintIdentity,
    RelationSemanticKey,
    ConstraintStrength,
    evaluate_constraint_strength,
    record_constraint_evaluation,
    NodeDescription,
    NodeDescriptionGraph,
    RelationObservation,
    RelationObservationStatus,
    RelationTargetScope,
    MatchingObservation,
    MatchingObservationStatus,
    TriggerDescription,
    observe_exact_keys,
    FunctionDescription,
    FunctionInvocation,
    RelationConstraintProfile,
    FunctionEvaluationComparison,
    RelationSimilarityObservation,
    RelationSemanticSimilarityObservation,
    SimilarityObservationStatus,
    compare_relation_constraint_profiles,
    compare_relation_semantic_keys,
    compare_relation_constraint_polarity,
    compare_relation_constraint_provenance,
    FunctionComparison,
    FunctionComposition,
    AdaptiveMBProfile,
    CompilationRecord,
    CompilationValidationStatus,
    FunctionCandidate,
    SimilarityMetric,
    SimilarityVector,
    StructureCandidate,
    StructureInductionResult,
    RelationClusterCandidate,
    RelationPatternCandidate,
    PatternSlotKind,
    PatternEvidence,
    PatternSlotEvidence,
    PatternVariableBinding,
    ConditionalRelationCandidate,
    ConditionalFunctionCandidate,
    ConditionObservationStatus,
    ConditionComposition,
    ConditionDescription,
    ConditionObservation,
    ConditionSet,
    evaluate_condition,
    ConditionSetObservation,
    evaluate_condition_set,
    ConditionalRuntimeObservation,
    record_conditional_runtime_observation,
    RuntimeMismatchSummary,
    RupturePolicyDescription,
    evaluate_runtime_rupture,
    ConditionalRelearningRequest,
    ConditionalRevisionCandidate,
    RelearningEvidenceAnalysis,
    ConditionalRevisionStatus,
    ConditionRevisionOperation,
    accept_conditional_revision,
    analyze_conditional_relearning,
    build_conditional_vnext_from_revision,
    request_conditional_relearning,
    reintroduce_conditional_to_adaptive,
    ConditionalStructureDelta,
    build_conditional_vnext,
    ConditionalSupersessionRecord,
    record_conditional_supersession,
    ExceptionCandidate,
    ObservedVariableBinding,
    ConditionalValidationStatus,
    ConditionalValidationRecord,
    ConditionalRuptureStatus,
    ConditionalRuptureRecord,
    ConditionalRuptureCoverage,
    StructureDelta,
    RecompiledStructureCandidate,
    RelationConstraintDelta,
    ProfileCorrespondence,
    compare_structure_candidates,
    extract_structure_candidate,
    induce_structure_candidate,
    cluster_relation_keys,
    derive_relation_pattern,
    build_conditional_relation_candidate,
    record_conditional_validation,
    compile_conditional_function_candidate,
    record_conditional_rupture,
    inspect_conditional_rupture_coverage,
    compile_conditionally_verified_function_candidate,
    compile_lineage_preserving_conditional_candidate,
    ConditionalCompilationRecord,
    ConditionalCompilationEvaluation,
    ConditionalCompiledMB,
    ConditionalPromotionRecord,
    ConditionalActivationRecord,
    record_conditional_compilation,
    record_evaluated_conditional_compilation,
    evaluate_conditional_compiled_promotion,
    activate_conditional_promotion_record,
    materialize_conditional_compiled_artifact,
    record_conditional_compilation_validation,
    materialize_conditional_compiled_mb,
    translate_conditional_ruptures_to_function,
    evaluate_conditional_promotion,
    activate_conditional_promotion,
    induce_structure_candidate_with_clusters,
    extract_recompiled_structure_candidate,
    compile_function_candidate,
    record_compilation_validation,
    CompiledMB,
    compile_validated_candidate,
    RuptureObservationStatus,
    record_rupture_observation,
    PromotionDecisionStatus,
    PromotionPolicyDescription,
    evaluate_promotion,
    ActiveCompiledMB,
    activate_promoted_artifact,
    DeactivationStatus,
    record_deactivation,
    RegistryStatus,
    project_current_function_state,
    request_recompilation,
    reintroduce_to_adaptive,
    compile_replacement_candidate,
    ReplacementCandidate,
    record_replacement_candidate,
    record_recompiled_replacement_candidate,
    CompiledReplacement,
    materialize_compiled_replacement,
    activate_compiled_replacement,
    SupersessionRecord,
    record_supersession,
)


class TestCoreContracts(unittest.TestCase):
    def test_core_contracts_have_no_enterprise_dependency(self):
        self.assertEqual(EvidencePolarity.UNRESOLVED.value, "unresolved")
        self.assertEqual(CommitmentOrigin.AUTHORITY.value, "authority")
        self.assertEqual(BoundaryContext("b1").boundary_id, "b1")
        self.assertEqual(Provenance("fixture").source, "fixture")
        self.assertEqual(AuthorityConstraint("a1", "workflow", "ticket", "approve").scope, "workflow")

    def test_function_description_is_bounded_evaluator_identity(self):
        function = FunctionDescription("rdl_core.exact_keys", "0")
        self.assertEqual(function.function_id, "rdl_core.exact_keys")
        self.assertEqual(function.version, "0")
        with self.assertRaises(TypeError):
            FunctionDescription(123, "0")
        with self.assertRaises(ValueError):
            FunctionDescription("", "0")

        constraint_function = FunctionDescription("rdl_core.constraint_strength", "0")
        evaluation = ConstraintEvaluation(
            strength=ConstraintStrength(0.5), relevance=0.5, freshness=0.5,
            authority=0.5, source=0.5, convergence=0.5,
            weights=ConstraintEvaluationWeights(), context=BoundaryContext("function"),
            evaluator=constraint_function,
        )
        self.assertIs(evaluation.evaluator, constraint_function)
        self.assertEqual(evaluation.evaluator_id, constraint_function.function_id)
        with self.assertRaises(ValueError):
            ConstraintEvaluation(
                strength=ConstraintStrength(0.5), relevance=0.5, freshness=0.5,
                authority=0.5, source=0.5, convergence=0.5,
                weights=ConstraintEvaluationWeights(), context=BoundaryContext("function"),
                evaluator=FunctionDescription("other", "1"),
                evaluator_id=constraint_function.function_id,
                evaluator_version=constraint_function.version,
            )

        invocation = FunctionInvocation(
            function=constraint_function,
            context=BoundaryContext("function-invocation"),
            purpose="bounded evaluation",
            config={"threshold": 0.7, "nested": {"mode": "local"}},
            provenance=Provenance("test-function"),
        )
        self.assertEqual(invocation.config["threshold"], 0.7)
        with self.assertRaises(TypeError):
            invocation.config["threshold"] = 0.1
        comparison = FunctionComparison(invocation, invocation)
        self.assertTrue(comparison.comparable)
        self.assertTrue(comparison.same_function)
        changed = FunctionInvocation(
            FunctionDescription("rdl_core.constraint_strength", "1"),
            invocation.context,
            purpose=invocation.purpose,
            config=invocation.config,
            provenance=invocation.provenance,
        )
        changed_comparison = FunctionComparison(invocation, changed)
        self.assertTrue(changed_comparison.comparable)
        self.assertFalse(changed_comparison.same_function)

        composition = FunctionComposition(
            invocation,
            FunctionInvocation(
                FunctionDescription("rdl_core.next", "0"),
                invocation.context,
                provenance=Provenance("test-function"),
            ),
            left_output="relation_profile",
            right_input="relation_profile",
        )
        self.assertTrue(composition.composable)
        self.assertTrue(composition.provenance_present)
        self.assertTrue(composition.provenance_compatible)
        self.assertFalse(FunctionComposition(
            invocation,
            composition.right,
            left_output="other",
            right_input="relation_profile",
        ).semantic_types_compatible)

    def test_relation_similarity_is_bounded_and_not_truth(self):
        identity = ConstraintIdentity("c-sim", "a", "supports", "b")
        left = RelationConstraintProfile(identity, ConstraintStrength(0.8, EvidencePolarity.SUPPORT))
        right = RelationConstraintProfile(identity, ConstraintStrength(0.6, EvidencePolarity.SUPPORT))
        observation = compare_relation_constraint_profiles(
            left, right, BoundaryContext("similarity"),
            provenance=Provenance("similarity-fixture"),
        )
        self.assertEqual(observation.status, SimilarityObservationStatus.SIMILAR)
        self.assertEqual(observation.coverage, 1.0)
        self.assertAlmostEqual(observation.score, 0.8)
        self.assertEqual(observation.invocation.context, observation.context)
        self.assertEqual(observation.invocation.function, observation.evaluator)
        semantic_observation = compare_relation_semantic_keys(
            identity.semantic_key,
            RelationSemanticKey("x", "supports", "b"),
            BoundaryContext("semantic-similarity"),
        )
        self.assertIsInstance(semantic_observation, RelationSemanticSimilarityObservation)
        self.assertAlmostEqual(semantic_observation.score, 2 / 3)
        self.assertEqual(semantic_observation.status, SimilarityObservationStatus.SIMILAR)
        clusters = cluster_relation_keys(
            (identity.semantic_key, RelationSemanticKey("x", "supports", "b")),
            (semantic_observation,),
            BoundaryContext("cluster"),
        )
        self.assertEqual(len(clusters), 1)
        self.assertIsInstance(clusters[0], RelationClusterCandidate)
        self.assertEqual(len(clusters[0].members), 2)
        self.assertTrue(clusters[0].connected)
        self.assertEqual(clusters[0].conflicting_edges, ())
        self.assertEqual(clusters[0].unresolved_edges, ())
        self.assertAlmostEqual(clusters[0].support_cohesion, 2 / 3)
        self.assertEqual(clusters[0].coverage, 1.0)
        pattern = derive_relation_pattern(clusters[0])
        self.assertIsInstance(pattern, RelationPatternCandidate)
        self.assertIsNone(pattern.subject)
        self.assertEqual(pattern.relation, "supports")
        self.assertEqual(pattern.object, "b")
        self.assertFalse(pattern.fully_specified)
        self.assertEqual(pattern.slot_kinds, (
            PatternSlotKind.VARIABLE, PatternSlotKind.FIXED, PatternSlotKind.FIXED,
        ))
        self.assertIsInstance(pattern.evidence, PatternEvidence)
        self.assertEqual(pattern.evidence.member_count, 2)
        self.assertAlmostEqual(pattern.evidence.specificity, 2 / 3)
        self.assertEqual(pattern.evidence.edge_count, 0)
        self.assertEqual(pattern.evidence.conflict_ratio, 0.0)
        self.assertEqual(pattern.evidence.unresolved_ratio, 0.0)
        self.assertTrue(all(isinstance(item, PatternSlotEvidence) for item in pattern.slot_evidence))
        self.assertEqual(pattern.slot_evidence[0].kind, PatternSlotKind.VARIABLE)
        self.assertEqual(pattern.slot_evidence[1].kind, PatternSlotKind.FIXED)

        conditional = build_conditional_relation_candidate(
            pattern, conditions=("subject is observed",),
            exceptions=(RelationSemanticKey("z", "supports", "b"),),
        )
        self.assertIsInstance(conditional, ConditionalRelationCandidate)
        self.assertEqual(conditional.conditions, ("subject is observed",))
        self.assertIsNotNone(conditional.evidence)
        self.assertEqual(conditional.variable_slots, ("subject",))
        self.assertEqual(conditional.variable_bindings[0].slot, "subject")
        self.assertEqual(conditional.variable_bindings[0].values, ("a", "x"))
        self.assertEqual(conditional.variable_bindings[0].observed_values, ("a", "x"))
        self.assertFalse(hasattr(conditional.variable_bindings[0], "complete_domain"))
        self.assertIs(ObservedVariableBinding, PatternVariableBinding)
        condition_function = FunctionDescription("rdl_core.condition_match", "1")
        condition = ConditionDescription(
            "condition-1", condition_function,
            FunctionInvocation(condition_function, conditional.context, purpose="condition"),
            operands=(("subject", {"observed": True}),),
            variable_slots=("subject",),
        )
        self.assertEqual(condition.operands[0][0], "subject")
        exact_observation = evaluate_condition(
            condition, {"subject": {"observed": True}}, conditional.context,
        )
        self.assertEqual(exact_observation.status, ConditionObservationStatus.MATCH)
        unresolved_observation = evaluate_condition(
            condition, {}, conditional.context,
        )
        self.assertEqual(unresolved_observation.status, ConditionObservationStatus.UNRESOLVED)
        not_match_observation = evaluate_condition(
            condition, {"subject": {"observed": False}}, conditional.context,
        )
        self.assertEqual(not_match_observation.status, ConditionObservationStatus.NOT_MATCH)
        condition_observation = ConditionObservation(
            condition, ConditionObservationStatus.UNRESOLVED, conditional.context,
        )
        self.assertEqual(condition_observation.status, ConditionObservationStatus.UNRESOLVED)
        exception = ExceptionCandidate(
            RelationSemanticKey("z", "supports", "b"), condition=condition,
            context=conditional.context, reason="finite exception observation",
        )
        self.assertEqual(exception.relation.object, "b")
        with self.assertRaises(ValueError):
            ExceptionCandidate(
                RelationSemanticKey("z", "supports", "b"), condition=condition,
                context=BoundaryContext("different-exception-boundary"),
            )
        structured = ConditionalRelationCandidate(
            pattern, structured_conditions=(condition,), exception_candidates=(exception,),
            context=conditional.context, evidence=pattern.evidence,
        )
        self.assertNotIn("missing_conditions", structured.validation_blockers)
        self.assertEqual(structured.structured_conditions, (condition,))
        condition_set = ConditionSet((condition,), ConditionComposition.AND)
        self.assertEqual(condition_set.composition, ConditionComposition.AND)
        structured_with_set = ConditionalRelationCandidate(
            pattern, condition_set=condition_set, exception_candidates=(exception,),
            context=conditional.context, evidence=pattern.evidence,
        )
        self.assertEqual(structured_with_set.structured_conditions, (condition,))
        other_function = FunctionDescription("rdl_core.other_condition", "1")
        same_id_other_meaning = ConditionDescription(
            "condition-1", other_function,
            FunctionInvocation(other_function, conditional.context, purpose="condition"),
        )
        with self.assertRaises(ValueError):
            record_conditional_validation(
                structured, ConditionalValidationStatus.PASSED,
                conditional.context,
                condition_observations=(ConditionObservation(
                    condition, ConditionObservationStatus.UNRESOLVED,
                    conditional.context,
                ),),
            )
        with self.assertRaises(ValueError):
            record_conditional_validation(
                structured, ConditionalValidationStatus.PASSED, conditional.context,
                condition_observations=(ConditionObservation(
                    same_id_other_meaning, ConditionObservationStatus.MATCH,
                    conditional.context,
                ),),
            )
        structured_record = record_conditional_validation(
            structured, ConditionalValidationStatus.PASSED,
            conditional.context,
            condition_observations=(ConditionObservation(
                condition, ConditionObservationStatus.MATCH,
                conditional.context,
            ),),
        )
        structured_function = compile_conditional_function_candidate(
            structured_record, FunctionDescription("rdl_core.structured_condition", "1"),
            purpose="structured condition preservation",
        )
        self.assertEqual(structured_function.structure.structured_conditions, (condition,))
        self.assertEqual(structured_function.structure.exception_candidates, (exception,))
        with self.assertRaises(ValueError):
            ConditionDescription(
                "", condition_function,
                FunctionInvocation(condition_function, conditional.context, purpose="condition"),
            )
        with self.assertRaises(TypeError):
            ConditionObservation(condition, "false", conditional.context)
        with self.assertRaises(ValueError):
            ConditionDescription(
                "duplicate-operands", condition_function,
                FunctionInvocation(condition_function, conditional.context, purpose="condition"),
                operands=(("subject", "a"), ("subject", "x")),
            )
        with self.assertRaises(ValueError):
            ConditionObservation(
                condition, ConditionObservationStatus.MATCH,
                BoundaryContext("different-condition-boundary"),
            )
        self.assertEqual(conditional.validation_blockers, ())
        self.assertTrue(conditional.eligible_for_validation)
        contextless = ConditionalRelationCandidate(
            pattern=pattern, conditions=("subject is observed",), evidence=pattern.evidence,
        )
        self.assertIn("missing_context", contextless.validation_blockers)
        self.assertFalse(contextless.eligible_for_validation)
        conditional_record = record_conditional_validation(
            conditional, ConditionalValidationStatus.PASSED, BoundaryContext("conditional-validation"),
        )
        self.assertIsInstance(conditional_record, ConditionalValidationRecord)
        function_candidate = compile_conditional_function_candidate(
            conditional_record,
            FunctionDescription("rdl_core.conditional_relation", "1"),
            purpose="conditional structure compilation",
        )
        self.assertEqual(function_candidate.invocation.purpose, "conditional structure compilation")
        self.assertEqual(function_candidate.structure.conditions, ("subject is observed",))
        self.assertEqual(function_candidate.structure.exceptions, (RelationSemanticKey("z", "supports", "b"),))
        rupture_record = record_conditional_rupture(
            conditional, ConditionalRuptureStatus.UNRESOLVED,
            BoundaryContext("conditional-rupture"), check_id="counterexample-v0",
        )
        self.assertIsInstance(rupture_record, ConditionalRuptureRecord)
        coverage = inspect_conditional_rupture_coverage(
            (rupture_record,), required_checks=("counterexample-v0",)
        )
        self.assertIsInstance(coverage, ConditionalRuptureCoverage)
        self.assertFalse(coverage.complete)
        self.assertEqual(coverage.unresolved_checks, ("counterexample-v0",))
        with self.assertRaises(ValueError):
            inspect_conditional_rupture_coverage(
                (rupture_record,), required_checks=("",)
            )
        verified_rupture = record_conditional_rupture(
            conditional, ConditionalRuptureStatus.NOT_DETECTED,
            BoundaryContext("conditional-rupture"), check_id="counterexample-v1",
        )
        verified_candidate = compile_conditionally_verified_function_candidate(
            conditional_record, (verified_rupture,),
            FunctionDescription("rdl_core.conditional_relation_verified", "1"),
            purpose="verified conditional compilation",
            required_checks=("counterexample-v1",),
        )
        self.assertEqual(verified_candidate.invocation.purpose, "verified conditional compilation")
        lineage_candidate = compile_lineage_preserving_conditional_candidate(
            conditional_record, (verified_rupture,),
            FunctionDescription("rdl_core.conditional_relation_lineage", "1"),
            purpose="lineage-preserving conditional compilation",
            required_checks=("counterexample-v1",),
        )
        self.assertIsInstance(lineage_candidate, ConditionalFunctionCandidate)
        self.assertEqual(lineage_candidate.conditional_candidate, conditional)
        self.assertEqual(lineage_candidate.rupture_coverage.detected_checks, ())
        self.assertTrue(lineage_candidate.rupture_coverage.complete)
        conditional_compilation = record_conditional_compilation(
            lineage_candidate, BoundaryContext("conditional-lineage-compilation"),
        )
        self.assertIsInstance(conditional_compilation, ConditionalCompilationRecord)
        conditional_artifact = materialize_conditional_compiled_artifact(conditional_compilation)
        self.assertIsInstance(conditional_artifact, ConditionalCompiledMB)
        self.assertEqual(conditional_artifact.candidate, lineage_candidate)
        self.assertEqual(conditional_artifact.conditional_validation, conditional_record)
        self.assertEqual(conditional_artifact.conditional_candidate, conditional)
        self.assertEqual(conditional_artifact.generic_compilation, conditional_compilation.generic_record)
        self.assertEqual(conditional_artifact.rupture_coverage, lineage_candidate.rupture_coverage)
        self.assertEqual(conditional_artifact.artifact, conditional_artifact.generic_artifact)
        condition_set_observation = evaluate_condition_set(
            condition_set, {"subject": {"observed": True}}, conditional.context,
        )
        self.assertEqual(condition_set_observation.status, ConditionObservationStatus.MATCH)
        with self.assertRaises(ValueError):
            ConditionSetObservation(
                condition_set, condition_set_observation.observations,
                ConditionObservationStatus.UNRESOLVED, conditional.context,
            )
        canonical_runtime = record_conditional_runtime_observation(
            conditional_artifact, condition_set,
            {"subject": {"observed": True}}, conditional.context,
            purpose="canonical runtime observation",
        )
        self.assertEqual(canonical_runtime.status, ConditionObservationStatus.MATCH)
        runtime_match = ConditionalRuntimeObservation(
            conditional_artifact, condition_set_observation,
            (("subject", {"observed": True}),), conditional.context,
            "conditional runtime test", lineage_candidate.function,
        )
        mismatch_set_observation = evaluate_condition_set(
            condition_set, {"subject": {"observed": False}}, conditional.context,
        )
        self.assertEqual(mismatch_set_observation.status, ConditionObservationStatus.NOT_MATCH)
        runtime_mismatch = ConditionalRuntimeObservation(
            conditional_artifact, mismatch_set_observation,
            (("subject", {"observed": False}),), conditional.context,
            "conditional runtime test", lineage_candidate.function,
        )
        summary = RuntimeMismatchSummary(
            conditional, (runtime_mismatch,), conditional.context,
        )
        policy = RupturePolicyDescription(
            lineage_candidate.function, (ConditionObservationStatus.NOT_MATCH,), 2,
            conditional.context, "runtime rupture threshold",
        )
        one_mismatch = evaluate_runtime_rupture(
            summary, policy, check_id="runtime-mismatch",
        )
        self.assertEqual(one_mismatch.status, ConditionalRuptureStatus.NOT_DETECTED)
        detected_summary = RuntimeMismatchSummary(
            conditional, (runtime_mismatch, runtime_mismatch), conditional.context,
        )
        detected = evaluate_runtime_rupture(
            detected_summary, policy, check_id="runtime-mismatch",
        )
        self.assertEqual(detected.status, ConditionalRuptureStatus.DETECTED)
        conditional_promotion = evaluate_conditional_compiled_promotion(
            conditional_artifact, (verified_rupture,),
            BoundaryContext("canonical-conditional-promotion"),
            required_checks=("counterexample-v1",),
        )
        self.assertIsInstance(conditional_promotion, ConditionalPromotionRecord)
        conditional_activation = activate_conditional_promotion_record(
            conditional_promotion, BoundaryContext("canonical-conditional-activation"),
        )
        self.assertIsInstance(conditional_activation, ConditionalActivationRecord)
        self.assertEqual(
            conditional_activation.active.artifact,
            conditional_artifact.generic_artifact,
        )
        relearning = request_conditional_relearning(
            conditional_activation, (detected,), conditional.context,
            reason="runtime mismatch threshold",
        )
        self.assertIsInstance(relearning, ConditionalRelearningRequest)
        adaptive_reentry = reintroduce_conditional_to_adaptive(
            relearning, (left,),
        )
        self.assertEqual(
            adaptive_reentry.prior_structure,
            conditional_artifact.generic_artifact.structure,
        )
        self.assertEqual(adaptive_reentry.recompilation_reason, "runtime mismatch threshold")
        self.assertEqual(adaptive_reentry.relearning_evidence, (detected,))
        revision = analyze_conditional_relearning(relearning)
        self.assertIsInstance(revision, ConditionalRevisionCandidate)
        self.assertIsInstance(revision.analysis, RelearningEvidenceAnalysis)
        self.assertIn("no_structural_revision_proposal", revision.unresolved_reasons)
        with self.assertRaises(ValueError):
            accept_conditional_revision(revision)
        proposed_revision = analyze_conditional_relearning(
            relearning, proposed_conditions=("runtime mismatch reviewed",),
        )
        self.assertEqual(proposed_revision.proposed_conditions, ("runtime mismatch reviewed",))
        self.assertEqual(proposed_revision.proposed_condition_additions, proposed_revision.proposed_conditions)
        self.assertEqual(proposed_revision.proposed_exception_additions, proposed_revision.proposed_exceptions)
        self.assertEqual(proposed_revision.unresolved_reasons, ())
        self.assertEqual(proposed_revision.supporting_ruptures, relearning.ruptures)
        self.assertGreaterEqual(len(proposed_revision.supporting_evidence), 2)
        self.assertEqual(proposed_revision.reconsidered_condition_ids, ("condition-1",))
        self.assertEqual(proposed_revision.proposed_condition_removals, ())
        self.assertEqual(
            proposed_revision.analysis.condition_proposals,
            (("condition-1", ConditionRevisionOperation.RECONSIDER),),
        )
        self.assertEqual(proposed_revision.status, ConditionalRevisionStatus.PROPOSED)
        with self.assertRaises(ValueError):
            build_conditional_vnext_from_revision(proposed_revision, pattern)
        accepted_revision = accept_conditional_revision(proposed_revision)
        self.assertEqual(accepted_revision.status, ConditionalRevisionStatus.ACCEPTED)
        revised_vnext, revised_delta = build_conditional_vnext_from_revision(
            accepted_revision, pattern,
        )
        self.assertEqual(
            revised_vnext.conditions,
            ("subject is observed", "runtime mismatch reviewed"),
        )
        self.assertEqual(revised_delta.added_conditions, ("runtime mismatch reviewed",))
        vnext, conditional_delta = build_conditional_vnext(
            relearning, pattern,
            conditions=("subject is observed", "runtime mismatch reviewed"),
        )
        self.assertIsInstance(conditional_delta, ConditionalStructureDelta)
        self.assertEqual(conditional_delta.added_conditions, ("runtime mismatch reviewed",))
        self.assertEqual(vnext.exceptions, conditional.exceptions)
        self.assertIsNot(vnext, conditional)
        inherited_vnext, _ = build_conditional_vnext(relearning, pattern)
        self.assertEqual(inherited_vnext.conditions, conditional.conditions)
        self.assertEqual(inherited_vnext.exceptions, conditional.exceptions)
        cleared_vnext, cleared_delta = build_conditional_vnext(
            relearning, pattern, conditions=(), exceptions=(),
        )
        self.assertEqual(cleared_vnext.conditions, ())
        self.assertEqual(cleared_vnext.exceptions, ())
        self.assertEqual(cleared_delta.removed_conditions, conditional.conditions)
        self.assertEqual(cleared_delta.removed_exceptions, conditional.exceptions)
        structured_delta = ConditionalStructureDelta(structured, structured_with_set)
        self.assertEqual(structured_delta.added_structured_conditions, ())
        self.assertEqual(structured_delta.removed_structured_conditions, ())
        self.assertFalse(structured_delta.pattern_changed)
        self.assertFalse(structured_delta.variable_bindings_changed)
        self.assertFalse(structured_delta.unresolved_slots_changed)
        self.assertTrue(structured_delta.condition_set_changed)
        self.assertFalse(structured_delta.evidence_changed)
        vnext_validation = record_conditional_validation(
            vnext, ConditionalValidationStatus.PASSED, conditional.context,
        )
        vnext_rupture = record_conditional_rupture(
            vnext, ConditionalRuptureStatus.NOT_DETECTED, conditional.context,
            check_id="vnext-counterexample",
        )
        vnext_lineage = compile_lineage_preserving_conditional_candidate(
            vnext_validation, (vnext_rupture,),
            FunctionDescription("rdl_core.conditional_relation_vnext", "2"),
            purpose="conditional vNext compilation",
            required_checks=("vnext-counterexample",),
        )
        vnext_evaluation = ConditionalCompilationEvaluation(
            vnext_lineage, CompilationValidationStatus.PASSED, conditional.context,
        )
        vnext_record = record_evaluated_conditional_compilation(
            vnext_lineage, vnext_evaluation,
        )
        vnext_artifact = materialize_conditional_compiled_artifact(vnext_record)
        vnext_promotion = evaluate_conditional_compiled_promotion(
            vnext_artifact, (vnext_rupture,), conditional.context,
            required_checks=("vnext-counterexample",),
        )
        vnext_activation = activate_conditional_promotion_record(
            vnext_promotion, conditional.context,
        )
        supersession = record_conditional_supersession(
            conditional_activation, vnext_activation, relearning, conditional_delta,
            conditional.context,
        )
        self.assertIsInstance(supersession, ConditionalSupersessionRecord)
        mismatched_generic = record_compilation_validation(
            lineage_candidate.function_candidate, CompilationValidationStatus.PASSED,
            BoundaryContext("different-compilation-boundary"),
        )
        with self.assertRaises(ValueError):
            ConditionalCompilationRecord(
                lineage_candidate, mismatched_generic,
                BoundaryContext("conditional-lineage-compilation"),
            )
        explicit_evaluation = ConditionalCompilationEvaluation(
            lineage_candidate, CompilationValidationStatus.PASSED,
            BoundaryContext("explicit-compilation"), reason="bounded compilation check",
        )
        explicit_record = record_evaluated_conditional_compilation(
            lineage_candidate, explicit_evaluation,
        )
        self.assertEqual(explicit_record.evaluation, explicit_evaluation)
        failed_evaluation = ConditionalCompilationEvaluation(
            lineage_candidate, CompilationValidationStatus.FAILED,
            BoundaryContext("failed-compilation"), reason="finite check failed",
        )
        failed_record = record_evaluated_conditional_compilation(
            lineage_candidate, failed_evaluation,
        )
        with self.assertRaises(ValueError):
            materialize_conditional_compiled_artifact(failed_record)
        compilation_record = record_conditional_compilation_validation(
            conditional_record, (verified_rupture,),
            FunctionDescription("rdl_core.conditional_relation_recorded", "1"),
            purpose="record conditional compilation",
            validation_context=BoundaryContext("conditional-compilation"),
            required_checks=("counterexample-v1",),
        )
        self.assertEqual(compilation_record.validation_status, CompilationValidationStatus.PASSED)
        conditional_compiled = materialize_conditional_compiled_mb(compilation_record)
        self.assertEqual(conditional_compiled.function.function_id, "rdl_core.conditional_relation_recorded")
        translated = translate_conditional_ruptures_to_function(
            verified_candidate, conditional, (verified_rupture,)
        )
        self.assertEqual(len(translated), 1)
        self.assertEqual(translated[0].check_id, "counterexample-v1")
        promotion = evaluate_conditional_promotion(
            conditional_compiled, compilation_record.candidate, conditional, (verified_rupture,),
            BoundaryContext("conditional-promotion"), required_checks=("counterexample-v1",),
        )
        self.assertEqual(promotion.status, PromotionDecisionStatus.APPROVED)
        active_conditional = activate_conditional_promotion(
            promotion, BoundaryContext("conditional-activation"),
        )
        self.assertEqual(active_conditional.artifact, conditional_compiled)
        generic_promotion = evaluate_promotion(
            conditional_artifact.generic_artifact,
            BoundaryContext("generic-conditional-promotion"),
            ruptures=tuple(
                translate_conditional_ruptures_to_function(
                    lineage_candidate.function_candidate, conditional, (verified_rupture,)
                )
            ),
            required_checks=("counterexample-v1",),
        )
        self.assertEqual(generic_promotion.status, PromotionDecisionStatus.APPROVED)
        generic_active = activate_promoted_artifact(
            generic_promotion, BoundaryContext("generic-conditional-activation"),
        )
        self.assertIsInstance(generic_active, ActiveCompiledMB)
        self.assertEqual(pattern.varying_slots, ("subject",))
        self.assertAlmostEqual(pattern.specificity, 2 / 3)
        clustered = induce_structure_candidate_with_clusters(
            AdaptiveMBProfile((left, right), BoundaryContext("adaptive")),
            BoundaryContext("cluster"),
            (observation,),
            clusters,
        )
        self.assertEqual(len(clustered.cluster_candidates), 1)
        self.assertAlmostEqual(clustered.cluster_candidates[0].cohesion, 2 / 3)
        induction = induce_structure_candidate(
            AdaptiveMBProfile((left, right), BoundaryContext("adaptive")),
            BoundaryContext("induction"),
            (observation,),
        )
        self.assertIsInstance(induction, StructureInductionResult)
        self.assertEqual(induction.common_relations, (identity.semantic_key,))
        self.assertEqual(induction.exception_relations, ())
        self.assertEqual(induction.unresolved_observations, ())
        unresolved = compare_relation_constraint_profiles(
            left,
            RelationConstraintProfile(identity, ConstraintStrength(0.6)),
            BoundaryContext("similarity"),
        )
        self.assertEqual(unresolved.status, SimilarityObservationStatus.UNRESOLVED)
        polarity = compare_relation_constraint_polarity(
            left, right, BoundaryContext("similarity"),
            invocation=FunctionInvocation(
                FunctionDescription("rdl_core.relation_polarity_similarity", "0"),
                BoundaryContext("similarity"),
            ),
        )
        self.assertEqual(polarity.status, SimilarityObservationStatus.SIMILAR)
        self.assertEqual(polarity.evaluator.function_id, "rdl_core.relation_polarity_similarity")
        provenance = compare_relation_constraint_provenance(
            left, right, BoundaryContext("similarity"),
        )
        self.assertEqual(provenance.status, SimilarityObservationStatus.UNRESOLVED)
        source_left = ConstraintIdentity(
            "c-left", "a", "supports", "b", Provenance("source-a")
        )
        source_right = ConstraintIdentity(
            "c-right", "a", "supports", "b", Provenance("source-b")
        )
        self.assertEqual(source_left.semantic_key, RelationSemanticKey("a", "supports", "b"))
        provenance_comparison = compare_relation_constraint_provenance(
            RelationConstraintProfile(source_left, left.strength),
            RelationConstraintProfile(source_right, right.strength),
            BoundaryContext("similarity"),
        )
        self.assertEqual(provenance_comparison.coverage, 1.0)
        self.assertEqual(provenance_comparison.status, SimilarityObservationStatus.NOT_SIMILAR)
        evaluation_comparison = FunctionEvaluationComparison(observation, polarity)
        self.assertTrue(evaluation_comparison.comparable)
        self.assertAlmostEqual(evaluation_comparison.delta(), -0.2)
        vector = SimilarityVector(strength=0.8, polarity=1.0)
        self.assertEqual(SimilarityMetric.STRENGTH.value, "strength")
        self.assertIsNone(vector.provenance)
        profile = AdaptiveMBProfile((left, right), BoundaryContext("adaptive"))
        structure = StructureCandidate((identity.semantic_key,), BoundaryContext("adaptive"))
        candidate_function = FunctionDescription("rdl_core.compiled_relation", "1")
        candidate_invocation = FunctionInvocation(candidate_function, BoundaryContext("adaptive"))
        candidate = FunctionCandidate(candidate_function, candidate_invocation, structure)
        record = CompilationRecord(candidate, validated=False, validation_context=BoundaryContext("validation"))
        self.assertEqual(len(profile.profiles), 2)
        self.assertFalse(record.validated)
        self.assertEqual(record.validation_status, CompilationValidationStatus.NOT_EVALUATED)
        with self.assertRaises(ValueError):
            CompilationRecord(
                candidate, validated=True,
                validation_context=BoundaryContext("validation"),
                validation_status=CompilationValidationStatus.FAILED,
            )
        extracted = extract_structure_candidate(
            profile, BoundaryContext("structure"),
            similarity=SimilarityVector(strength=0.9),
        )
        self.assertEqual(extracted.relations, (identity.semantic_key,))
        self.assertEqual(extracted.similarity.strength, 0.9)
        self.assertEqual(len(extracted.supporting_profiles), 2)
        candidate = compile_function_candidate(
            extracted, FunctionDescription("rdl_core.compiled_relation", "1"),
            purpose="structure compilation", config={"mode": "bounded"},
        )
        self.assertEqual(candidate.invocation.purpose, "structure compilation")
        delta = compare_structure_candidates(
            extracted, StructureCandidate(
                (identity.semantic_key,), BoundaryContext("structure"),
                supporting_profiles=(left, right),
            )
        )
        self.assertEqual(delta.added, ())
        self.assertEqual(delta.unchanged, (identity.semantic_key,))
        self.assertEqual(len(delta.constraint_deltas), 0)
        self.assertIsInstance(delta.profile_correspondences[0], ProfileCorrespondence)
        self.assertEqual(len(delta.profile_correspondences[0].unmatched_previous), 2)
        self.assertEqual(len(delta.profile_correspondences[0].unmatched_current), 2)
        failed_record = record_compilation_validation(
            candidate, CompilationValidationStatus.FAILED, BoundaryContext("validation"),
        )
        self.assertFalse(failed_record.validated)
        self.assertEqual(failed_record.validation_status, CompilationValidationStatus.FAILED)
        rupture = record_rupture_observation(
            candidate, RuptureObservationStatus.UNRESOLVED,
            BoundaryContext("rupture"), reason="missing counterfactual input",
        )
        self.assertEqual(rupture.status, RuptureObservationStatus.UNRESOLVED)
        self.assertEqual(rupture.evaluator.function_id, "rdl_core.rupture_check")
        passed_record = record_compilation_validation(
            candidate, CompilationValidationStatus.PASSED, BoundaryContext("validation"),
        )
        compiled = compile_validated_candidate(passed_record)
        self.assertIsInstance(compiled, CompiledMB)
        with self.assertRaises(ValueError):
            compile_validated_candidate(failed_record)
        approved = compile_validated_candidate(passed_record)
        decision = evaluate_promotion(approved, BoundaryContext("promotion"))
        self.assertEqual(decision.status, PromotionDecisionStatus.NOT_EVALUATED)
        checked_decision = evaluate_promotion(
            approved, BoundaryContext("promotion"),
            ruptures=(record_rupture_observation(
                candidate, RuptureObservationStatus.NOT_DETECTED,
                BoundaryContext("rupture"), check_id="counterexample",
            ),),
            required_checks=("counterexample",),
        )
        self.assertEqual(checked_decision.status, PromotionDecisionStatus.APPROVED)
        policy_description = PromotionPolicyDescription(
            FunctionDescription("rdl_core.promotion_policy", "1"),
            required_checks=("counterexample",),
        )
        policy_checked = evaluate_promotion(
            approved, BoundaryContext("promotion"),
            policy=policy_description.function,
            policy_description=policy_description,
            ruptures=(record_rupture_observation(
                candidate, RuptureObservationStatus.NOT_DETECTED,
                BoundaryContext("rupture"), check_id="counterexample",
            ),),
        )
        self.assertEqual(policy_checked.status, PromotionDecisionStatus.APPROVED)
        with self.assertRaises(ValueError):
            evaluate_promotion(
                approved, BoundaryContext("promotion"),
                ruptures=(record_rupture_observation(
                    FunctionCandidate(
                        FunctionDescription("rdl_core.other", "0"),
                        FunctionInvocation(FunctionDescription("rdl_core.other", "0"), BoundaryContext("adaptive")),
                        structure,
                    ), RuptureObservationStatus.NOT_DETECTED,
                    BoundaryContext("rupture"), check_id="counterexample",
                ),),
            )
        unresolved_decision = evaluate_promotion(
            approved, BoundaryContext("promotion"), ruptures=(rupture,)
        )
        self.assertEqual(unresolved_decision.status, PromotionDecisionStatus.UNRESOLVED)
        active = activate_promoted_artifact(checked_decision, BoundaryContext("activation"))
        self.assertIsInstance(active, ActiveCompiledMB)
        with self.assertRaises(ValueError):
            activate_promoted_artifact(unresolved_decision, BoundaryContext("activation"))
        deactivation = record_deactivation(
            active, DeactivationStatus.DEACTIVATED,
            BoundaryContext("deactivation"), reason="recompile requested",
        )
        self.assertEqual(deactivation.status, DeactivationStatus.DEACTIVATED)
        self.assertEqual(project_current_function_state(active).status, RegistryStatus.ACTIVE)
        self.assertEqual(
            project_current_function_state(active, deactivation=deactivation).status,
            RegistryStatus.INACTIVE,
        )
        replacement = activate_promoted_artifact(checked_decision, BoundaryContext("activation-next"))
        with self.assertRaises(ValueError):
            project_current_function_state(active, replacement=replacement)
        request = request_recompilation(
            active, deactivation, BoundaryContext("recompile"),
            reason="new boundary observed",
        )
        self.assertEqual(request.active, active)
        adaptive_next = reintroduce_to_adaptive(
            request, (left, right), BoundaryContext("adaptive-next"),
        )
        self.assertEqual(len(adaptive_next.profiles), 2)
        self.assertEqual(adaptive_next.prior_structure, active.artifact.structure)
        self.assertEqual(adaptive_next.recompilation_reason, "new boundary observed")
        rebuilt = extract_recompiled_structure_candidate(
            adaptive_next, BoundaryContext("structure"), similarity=SimilarityVector(strength=0.95),
        )
        self.assertIsInstance(rebuilt, RecompiledStructureCandidate)
        rebuilt_lineage = record_recompiled_replacement_candidate(
            request, rebuilt, FunctionDescription("rdl_core.compiled_relation", "2"),
        )
        self.assertEqual(rebuilt_lineage.structure_delta.unchanged, (identity.semantic_key,))
        replacement_candidate = compile_replacement_candidate(
            request,
            StructureCandidate((identity.semantic_key,), BoundaryContext("recompile")),
            FunctionDescription("rdl_core.compiled_relation", "2"),
        )
        self.assertEqual(replacement_candidate.function.version, "2")
        replacement_lineage = record_replacement_candidate(
            request,
            StructureCandidate((identity.semantic_key,), BoundaryContext("structure")),
            FunctionDescription("rdl_core.compiled_relation", "2"),
        )
        self.assertIsInstance(replacement_lineage, ReplacementCandidate)
        with self.assertRaises(ValueError):
            record_replacement_candidate(
                request,
                StructureCandidate((identity.semantic_key,), BoundaryContext("adaptive")),
                FunctionDescription("rdl_core.compiled_relation", "1"),
            )
        replacement_validation = record_compilation_validation(
            replacement_lineage.candidate, CompilationValidationStatus.PASSED,
            BoundaryContext("validation-v2"),
        )
        compiled_replacement = materialize_compiled_replacement(
            replacement_lineage, replacement_validation,
        )
        self.assertIsInstance(compiled_replacement, CompiledReplacement)
        replacement_promotion = evaluate_promotion(
            compiled_replacement.compiled, BoundaryContext("promotion-v2"),
            ruptures=(record_rupture_observation(
                replacement_lineage.candidate, RuptureObservationStatus.NOT_DETECTED,
                BoundaryContext("rupture-v2"), check_id="counterexample",
            ),),
            required_checks=("counterexample",),
        )
        active_v2 = activate_compiled_replacement(
            compiled_replacement, replacement_promotion, BoundaryContext("activation-v2"),
        )
        self.assertEqual(active_v2.artifact, compiled_replacement.compiled)
        supersession = SupersessionRecord(
            active, active_v2, compiled_replacement, BoundaryContext("supersession"),
        )
        self.assertEqual(supersession.predecessor, active)
        self.assertEqual(
            record_supersession(
                active, active_v2, compiled_replacement, BoundaryContext("supersession"),
            ).replacement,
            active_v2,
        )


    def test_commitment_record_requires_valid_origin_and_time(self):
        record = CommitmentRecord.from_dict_strict(
            {
                "origin": "authority",
                "committed_at": "2026-09-09T00:00:00+00:00",
                "actor": "operator-1",
            }
        )
        self.assertEqual(record.origin, "authority")
        with self.assertRaises(ValueError):
            CommitmentRecord.from_dict_strict(
                {"origin": "truth", "committed_at": "bad", "actor": "operator-1"}
            )

    def test_domain_origin_is_namespaced_and_boundary_conditions_are_deep_frozen(self):
        record = CommitmentRecord.from_dict_strict(
            {
                "origin": "game:rumor",
                "committed_at": "2026-09-09T00:00:00+00:00",
                "actor": "npc-1",
            }
        )
        self.assertEqual(record.origin, "game:rumor")
        context = BoundaryContext("b1", conditions={"seed": 1, "nested": {"mode": "test"}})
        with self.assertRaises(TypeError):
            context.conditions["seed"] = 999
        with self.assertRaises(TypeError):
            context.conditions["nested"]["mode"] = "mutated"

        tuple_context = BoundaryContext("b2", conditions={"items": ({"seed": 1},)})
        with self.assertRaises(TypeError):
            tuple_context.conditions["items"][0]["seed"] = 2
        with self.assertRaises(TypeError):
            BoundaryContext("b3", conditions={"unsupported": object()})

    def test_scenario_02_similarity_supports_rupture_inspection_without_deciding_it(self):
        context = BoundaryContext("scenario-02")
        identity = ConstraintIdentity("scenario-02", "partner-a", "approves", "invoice")
        profile = RelationConstraintProfile(
            identity, ConstraintStrength(0.9, EvidencePolarity.SUPPORT)
        )
        similarity = compare_relation_constraint_profiles(
            profile, profile, context,
            provenance=Provenance("scenario-02-similarity"),
        )
        self.assertEqual(similarity.status, SimilarityObservationStatus.SIMILAR)
        self.assertGreater(similarity.score, 0.0)

        structure = StructureCandidate(
            (identity.semantic_key,), context,
            supporting_profiles=(profile,),
        )
        function = FunctionDescription("enterprise.approval", "1")
        candidate = FunctionCandidate(
            function,
            FunctionInvocation(function, context, purpose="scenario-02 approval"),
            structure,
        )
        validation = record_compilation_validation(
            candidate, CompilationValidationStatus.PASSED, context,
        )
        artifact = compile_validated_candidate(validation)

        detected = record_rupture_observation(
            candidate, RuptureObservationStatus.DETECTED, context,
            check_id="scenario-02-counterexample",
            reason="runtime counterexample requires inspection",
        )
        rejected = evaluate_promotion(
            artifact, context,
            ruptures=(detected,),
            required_checks=("scenario-02-counterexample",),
        )
        self.assertEqual(rejected.status, PromotionDecisionStatus.REJECTED)
        self.assertEqual(similarity.status, SimilarityObservationStatus.SIMILAR)

        not_detected = record_rupture_observation(
            candidate, RuptureObservationStatus.NOT_DETECTED, context,
            check_id="scenario-02-counterexample",
        )
        approved = evaluate_promotion(
            artifact, context,
            ruptures=(not_detected,),
            required_checks=("scenario-02-counterexample",),
        )
        self.assertEqual(approved.status, PromotionDecisionStatus.APPROVED)

    def test_scenario_03_unresolved_probe_re_evaluates_without_failure_collapse(self):
        context = BoundaryContext("scenario-03")
        function = FunctionDescription("rdl_core.partner_condition", "1")
        condition = ConditionDescription(
            "partner-confirmed",
            function,
            FunctionInvocation(function, context, purpose="scenario-03 condition"),
            operands=(("partner", "confirmed"),),
            variable_slots=("partner",),
        )

        unresolved = evaluate_condition(condition, {}, context)
        self.assertEqual(unresolved.status, ConditionObservationStatus.UNRESOLVED)
        self.assertNotEqual(unresolved.status, ConditionObservationStatus.NOT_MATCH)

        # A Probe supplies a finite observation; it does not commit a proposition.
        probe_provenance = Provenance("scenario-03-probe")
        probed = evaluate_condition(
            condition, {"partner": "confirmed"}, context,
            provenance=probe_provenance,
        )
        self.assertEqual(probed.status, ConditionObservationStatus.MATCH)
        self.assertEqual(probed.condition, condition)
        self.assertEqual(probed.context, context)
        self.assertEqual(probed.provenance, probe_provenance)

    def test_scenario_03_enterprise_probe_adapter_preserves_core_provenance(self):
        from rdl_enterprise.mb_graph import MBNode
        from rdl_enterprise.trigger_adapter import matching_observation_from_mbnode

        node = MBNode(
            id="scenario-03-probe-node",
            domain="workflow",
            trigger_pattern={"exact_keys": ["approval", "confirmed"]},
            action_template={"type": "ask_human"},
            source_id="enterprise:probe",
            source_lineage="probe-v1",
        )
        observation = matching_observation_from_mbnode(
            node,
            "approval confirmed",
            BoundaryContext("scenario-03-adapter"),
        )
        self.assertEqual(observation.status, MatchingObservationStatus.MATCHED)
        self.assertIsNotNone(observation.provenance)
        self.assertEqual(observation.provenance.source, "enterprise:probe")
        self.assertEqual(observation.provenance.lineage, "probe-v1")

    def test_stress_scenario_x_conflicting_evidence_and_boundary_drift_stay_unresolved(self):
        boundary_b1 = BoundaryContext("stress-b1", question="approve invoice", purpose="cruise")
        boundary_b2 = BoundaryContext("stress-b2", question="approve invoice with regulation", purpose="inspection")
        semantic = RelationSemanticKey("partner-a", "approves", "invoice")
        official = ConstraintIdentity(
            "s1", semantic.subject, semantic.relation, semantic.object,
            Provenance("official-s1", lineage="policy-older"),
        )
        field = ConstraintIdentity(
            "s2", semantic.subject, semantic.relation, semantic.object,
            Provenance("field-s2", lineage="observation-newer"),
        )
        official_profile = RelationConstraintProfile(
            official, ConstraintStrength(
                0.9, EvidencePolarity.SUPPORT,
                relevance=0.8, freshness=0.2, authority=0.95,
            )
        )
        field_profile = RelationConstraintProfile(
            field, ConstraintStrength(
                0.8, EvidencePolarity.OPPOSE,
                relevance=0.9, freshness=0.95, authority=0.3,
            )
        )

        strength_similarity = compare_relation_constraint_profiles(
            official_profile, field_profile, boundary_b1,
            provenance=Provenance("stress-strength"),
        )
        polarity_similarity = compare_relation_constraint_polarity(
            official_profile, field_profile, boundary_b1,
            provenance=Provenance("stress-polarity"),
        )
        provenance_similarity = compare_relation_constraint_provenance(
            official_profile, field_profile, boundary_b1,
            provenance=Provenance("stress-provenance"),
        )
        self.assertEqual(strength_similarity.status, SimilarityObservationStatus.SIMILAR)
        self.assertEqual(polarity_similarity.status, SimilarityObservationStatus.NOT_SIMILAR)
        self.assertEqual(provenance_similarity.status, SimilarityObservationStatus.NOT_SIMILAR)
        self.assertNotEqual(
            compare_relation_constraint_profiles(
                official_profile, field_profile, boundary_b2,
            ).context,
            strength_similarity.context,
        )

        missing_source = ConstraintIdentity(
            "s3", semantic.subject, semantic.relation, semantic.object,
        )
        missing_profile = RelationConstraintProfile(
            missing_source, ConstraintStrength(0.7, EvidencePolarity.UNRESOLVED),
        )
        self.assertEqual(
            compare_relation_constraint_provenance(
                official_profile, missing_profile, boundary_b1,
            ).status,
            SimilarityObservationStatus.UNRESOLVED,
        )

        profile = AdaptiveMBProfile((official_profile, field_profile, missing_profile), boundary_b1)
        structure = extract_structure_candidate(profile, boundary_b1)
        function = FunctionDescription("enterprise.stress.approval", "1")
        candidate = FunctionCandidate(
            function,
            FunctionInvocation(function, boundary_b1, purpose="stress runtime"),
            structure,
        )
        validation = record_compilation_validation(
            candidate, CompilationValidationStatus.PASSED, boundary_b1,
        )
        artifact = compile_validated_candidate(validation)
        unresolved_rupture = record_rupture_observation(
            candidate, RuptureObservationStatus.UNRESOLVED, boundary_b2,
            check_id="stress-boundary-drift",
            reason="new boundary and conflicting evidence require inspection",
        )
        decision = evaluate_promotion(
            artifact, boundary_b2,
            ruptures=(unresolved_rupture,),
            required_checks=("stress-boundary-drift",),
        )
        self.assertEqual(decision.status, PromotionDecisionStatus.UNRESOLVED)

    def test_stress_scenario_y_inconsistent_complainant_preserves_conflicting_demands(self):
        t1 = BoundaryContext("complaint-t1", question="refund", purpose="customer-resolution")
        t2 = BoundaryContext("complaint-t2", question="retain-service", purpose="customer-retention")
        customer = "customer-17"
        demands = (
            RelationConstraintProfile(
                ConstraintIdentity(
                    "d-refund", customer, "demands", "refund",
                    Provenance("customer-statement", lineage="t1"),
                ),
                ConstraintStrength(0.8, EvidencePolarity.SUPPORT),
            ),
            RelationConstraintProfile(
                ConstraintIdentity(
                    "d-no-cancel", customer, "demands", "no-cancellation",
                    Provenance("customer-statement", lineage="t1"),
                ),
                ConstraintStrength(0.8, EvidencePolarity.SUPPORT),
            ),
            RelationConstraintProfile(
                ConstraintIdentity(
                    "d-keep-use", customer, "demands", "continued-use",
                    Provenance("customer-statement", lineage="t2"),
                ),
                ConstraintStrength(0.7, EvidencePolarity.SUPPORT),
            ),
            RelationConstraintProfile(
                ConstraintIdentity(
                    "d-exception", customer, "demands", "policy-exception",
                    Provenance("customer-statement", lineage="t2"),
                ),
                ConstraintStrength(0.6, EvidencePolarity.SUPPORT),
            ),
            RelationConstraintProfile(
                ConstraintIdentity(
                    "p-no-exception", "policy", "requires", "no-policy-exception",
                    Provenance("enterprise-policy", lineage="policy-v1"),
                ),
                ConstraintStrength(0.95, EvidencePolarity.SUPPORT, authority=0.95),
            ),
            RelationConstraintProfile(
                ConstraintIdentity(
                    "p-exception", "policy", "permits", "policy-exception",
                    Provenance("enterprise-policy", lineage="policy-v1"),
                ),
                ConstraintStrength(0.4, EvidencePolarity.OPPOSE, authority=0.95),
            ),
        )
        profile = AdaptiveMBProfile(demands, t2, provenance=Provenance("complaint-case"))
        structure = extract_structure_candidate(profile, t2)

        self.assertEqual(len(structure.relations), len(demands))
        self.assertEqual(len(structure.supporting_profiles), 5)
        self.assertEqual(len(structure.conflicting_profiles), 1)
        self.assertIsNotNone(structure.provenance)
        self.assertEqual(structure.provenance.source, "complaint-case")

        # A changed customer purpose is a different invocation boundary, not an overwrite.
        function = FunctionDescription("enterprise.complaint.selection", "1")
        refund_invocation = FunctionInvocation(function, t1, purpose="refund-resolution")
        retention_invocation = FunctionInvocation(function, t2, purpose="service-retention")
        comparison = FunctionComparison(refund_invocation, retention_invocation)
        self.assertFalse(comparison.same_boundary)
        self.assertFalse(comparison.same_purpose)
        self.assertFalse(comparison.comparable)

        # Conflicting demands remain evidence; no arbitrary resolution is produced.
        self.assertNotEqual(
            structure.supporting_profiles[0].identity.semantic_key,
            structure.conflicting_profiles[0].identity.semantic_key,
        )

    def test_stress_scenario_y_same_relation_conflict_is_observed_not_resolved(self):
        context = BoundaryContext("complaint-conflict")
        support = RelationConstraintProfile(
            ConstraintIdentity(
                "customer-support", "customer-17", "requires", "refund",
                Provenance("customer-statement", lineage="t1"),
            ),
            ConstraintStrength(0.85, EvidencePolarity.SUPPORT),
        )
        oppose = RelationConstraintProfile(
            ConstraintIdentity(
                "policy-opposition", "customer-17", "requires", "refund",
                Provenance("enterprise-policy", lineage="policy-v1"),
            ),
            ConstraintStrength(0.9, EvidencePolarity.OPPOSE),
        )
        strength = compare_relation_constraint_profiles(
            support, oppose, context,
            provenance=Provenance("complaint-strength"),
        )
        polarity = compare_relation_constraint_polarity(
            support, oppose, context,
            provenance=Provenance("complaint-polarity"),
        )
        self.assertEqual(strength.status, SimilarityObservationStatus.SIMILAR)
        self.assertEqual(strength.conflict, 1.0)
        self.assertEqual(polarity.status, SimilarityObservationStatus.NOT_SIMILAR)
        self.assertEqual(support.identity.semantic_key, oppose.identity.semantic_key)
        self.assertNotEqual(support.strength.support, oppose.strength.support)

    def test_scenario_02_similarity_is_t1_inspection_material(self):
        context = BoundaryContext("scenario-02-inspection")
        current = RelationConstraintProfile(
            ConstraintIdentity("current-case", "partner-a", "approves", "invoice"),
            ConstraintStrength(0.8, EvidencePolarity.SUPPORT),
        )
        prior = RelationConstraintProfile(
            ConstraintIdentity("prior-case", "partner-a", "approves", "invoice"),
            ConstraintStrength(0.7, EvidencePolarity.SUPPORT),
        )
        similarity = compare_relation_constraint_profiles(
            current, prior, context,
            provenance=Provenance("scenario-02-history"),
        )
        self.assertEqual(similarity.status, SimilarityObservationStatus.SIMILAR)

        profile = AdaptiveMBProfile((current, prior), context)
        inspected = induce_structure_candidate(
            profile, context, (similarity,), min_score=0.5,
        )
        self.assertIn(current.identity.semantic_key, inspected.common_relations)
        self.assertEqual(inspected.unresolved_observations, ())
        self.assertIsNotNone(inspected.candidate)

        # The T1 inspection material is retained as candidate evidence only.
        function = FunctionDescription("enterprise.approval.inspected", "1")
        candidate = FunctionCandidate(
            function,
            FunctionInvocation(function, context, purpose="scenario-02 inspection"),
            inspected.candidate,
        )
        rupture = record_rupture_observation(
            candidate, RuptureObservationStatus.DETECTED, context,
            check_id="scenario-02-inspection-counterexample",
        )
        self.assertEqual(rupture.status, RuptureObservationStatus.DETECTED)
        self.assertEqual(similarity.status, SimilarityObservationStatus.SIMILAR)

    def test_scenario_02_rupture_then_similarity_inspection_material(self):
        context = BoundaryContext("scenario-02-orchestration")
        current = RelationConstraintProfile(
            ConstraintIdentity("current-case", "partner-a", "approves", "invoice"),
            ConstraintStrength(0.8, EvidencePolarity.SUPPORT),
        )
        profile = AdaptiveMBProfile((current,), context)
        initial_structure = extract_structure_candidate(profile, context)
        function = FunctionDescription("enterprise.approval.runtime", "1")
        candidate = FunctionCandidate(
            function,
            FunctionInvocation(function, context, purpose="scenario-02 runtime"),
            initial_structure,
        )
        rupture = record_rupture_observation(
            candidate, RuptureObservationStatus.DETECTED, context,
            check_id="scenario-02-runtime-counterexample",
        )
        self.assertEqual(rupture.status, RuptureObservationStatus.DETECTED)

        prior = RelationConstraintProfile(
            ConstraintIdentity("prior-case", "partner-a", "approves", "invoice"),
            ConstraintStrength(0.7, EvidencePolarity.SUPPORT),
        )
        similarity = compare_relation_constraint_profiles(
            current, prior, context,
            provenance=Provenance("scenario-02-prior-case"),
        )
        inspected = induce_structure_candidate(
            AdaptiveMBProfile((current, prior), context),
            context,
            (similarity,),
        )
        self.assertEqual(similarity.status, SimilarityObservationStatus.SIMILAR)
        self.assertIn(current.identity.semantic_key, inspected.common_relations)
        self.assertEqual(rupture.status, RuptureObservationStatus.DETECTED)

    def test_core_source_has_no_runtime_package_imports(self):
        from pathlib import Path

        core_root = Path(__file__).parents[1] / "src" / "rdl_core"
        forbidden = {"rdl_enterprise", "rdl_simulation"}
        for path in core_root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported = {alias.name.split(".")[0] for alias in node.names}
                elif isinstance(node, ast.ImportFrom):
                    imported = {node.module.split(".")[0]} if node.module else set()
                else:
                    continue
                self.assertTrue(forbidden.isdisjoint(imported), f"forbidden import in {path}: {imported & forbidden}")

    def test_legacy_contract_compatibility_fixtures(self):
        fixtures = [
            ({"origin": "authority", "committed_at": "2026-09-09T00:00:00+00:00", "actor": "a1"}, True),
            ({"origin": "truth", "committed_at": "2026-09-09T00:00:00+00:00", "actor": "a1"}, False),
            ({"origin": "authority", "committed_at": "bad", "actor": "a1"}, False),
            ({"origin": "authority", "committed_at": "2026-09-09T00:00:00+00:00", "actor": "a1", "evidence_at": 123}, False),
        ]
        for payload, accepted in fixtures:
            try:
                record = CommitmentRecord.from_dict_strict(payload)
                observed = True
                serialized = record.to_dict()
            except ValueError:
                observed = False
                serialized = None
            self.assertEqual(observed, accepted, payload)
            if accepted:
                self.assertEqual(serialized["origin"], payload["origin"])

    def test_intentional_namespaced_origin_expansion(self):
        record = CommitmentRecord.from_dict_strict(
            {"origin": "game:rumor", "committed_at": "2026-09-09T00:00:00+00:00", "actor": "npc-1"}
        )
        self.assertEqual(record.origin, "game:rumor")

    def test_constraint_identity_and_bounded_activation(self):
        identity = ConstraintIdentity("c1", "requester", "may_approve", "expense")
        activation = ConstraintActivation(
            identity=identity,
            context=BoundaryContext("b1", question="approval"),
            strength=ConstraintStrength(value=0.8, support=EvidencePolarity.SUPPORT, relevance=0.9),
            authority_constraint=AuthorityConstraint("a1", "finance", "expense", "approve"),
        )
        self.assertEqual(activation.identity.constraint_id, "c1")
        with self.assertRaises(ValueError):
            ConstraintStrength(value=1.1)
        with self.assertRaises(TypeError):
            ConstraintStrength(value=0.5, support="truth")
        with self.assertRaises(ValueError):
            ConstraintIdentity("c2", 123, "relates", "object")

    def test_constraint_strength_evaluation_is_bounded_and_polarity_explicit(self):
        strength = evaluate_constraint_strength(
            relevance=1.0,
            freshness=0.5,
            authority=0.0,
            source=0.5,
            convergence=1.0,
            support=EvidencePolarity.OPPOSE,
            weights=ConstraintEvaluationWeights(
                relevance=1.0, freshness=1.0, authority=0.0, source=0.0, convergence=0.0
            ),
        )
        self.assertEqual(strength.support, EvidencePolarity.OPPOSE)
        self.assertEqual(strength.value, 0.75)
        with self.assertRaises(ValueError):
            evaluate_constraint_strength(
                relevance=1.1, freshness=0.5, authority=0.5, source=0.5, convergence=0.5
            )

    def test_constraint_evaluation_retains_inputs_and_evaluator_delta(self):
        context = BoundaryContext("eval-1", question="approval", purpose="compare")
        evaluation = record_constraint_evaluation(
            context=context, relevance=1.0, freshness=0.5, authority=0.0,
            source=0.5, convergence=1.0, provenance=Provenance("enterprise-bundle"),
        )
        self.assertIsInstance(evaluation, ConstraintEvaluation)
        self.assertEqual(evaluation.source, 0.5)
        self.assertEqual(evaluation.convergence, 1.0)
        self.assertEqual(evaluation.context.boundary_id, "eval-1")
        self.assertEqual(evaluation.evaluator_id, "rdl_core.constraint_strength")
        left = record_constraint_evaluation(
            context=context, relevance=1.0, freshness=0.5, authority=0.0,
            source=0.5, convergence=1.0, evaluator_id="legacy.enterprise", evaluator_version="1",
        )
        delta = ConstraintEvaluationDelta(left, evaluation)
        self.assertAlmostEqual(delta.value, left.strength.value - evaluation.strength.value)
        self.assertTrue(delta.same_boundary)
        comparison = ConstraintEvaluationComparison(left, evaluation)
        self.assertTrue(comparison.eligible)
        self.assertTrue(comparison.same_observations)
        self.assertTrue(comparison.same_evaluator_config)
        self.assertEqual(comparison.delta().value, delta.value)
        different_context = record_constraint_evaluation(
            context=BoundaryContext("eval-2"), relevance=1.0, freshness=0.5,
            authority=0.0, source=0.5, convergence=1.0,
        )
        self.assertFalse(ConstraintEvaluationComparison(left, different_context).eligible)
        self.assertTrue(ConstraintEvaluationComparison(left, different_context).raw_delta())
        with self.assertRaises(ValueError):
            ConstraintEvaluationComparison(left, different_context).delta()
        different_weights = record_constraint_evaluation(
            context=context, relevance=1.0, freshness=0.5, authority=0.0,
            source=0.5, convergence=1.0,
            weights=ConstraintEvaluationWeights(relevance=1.0, freshness=0.0, authority=0.0, source=0.0, convergence=0.0),
        )
        weight_comparison = ConstraintEvaluationComparison(left, different_weights)
        self.assertFalse(weight_comparison.same_evaluator_config)
        self.assertTrue(weight_comparison.same_observations)
        self.assertTrue(weight_comparison.evaluator_comparison_eligible)
        with self.assertRaises(TypeError):
            record_constraint_evaluation(
                context=context, relevance=1.0, freshness=0.5, authority=0.0,
                source=0.5, convergence=1.0, evaluator_id=123,
            )

    def test_enterprise_bundle_adapter_preserves_bounded_observation(self):
        from types import SimpleNamespace
        from rdl_enterprise.constraint_adapter import activation_from_bundle

        bundle = SimpleNamespace(
            constraint_score=0.7,
            relevance=0.8,
            freshness=0.6,
            authority_weight=0.4,
        )
        activation = activation_from_bundle(
            bundle,
            ConstraintIdentity("c-adapter", "requester", "may_approve", "expense"),
            BoundaryContext("b-adapter", question="approval"),
            support=EvidencePolarity.UNRESOLVED,
        )
        self.assertEqual(activation.strength.value, 0.7)
        self.assertEqual(activation.strength.support, EvidencePolarity.UNRESOLVED)

    def test_enterprise_core_bounded_equivalence_selected_observation(self):
        from rdl_enterprise.constraint import ConstraintBundle
        from rdl_enterprise.constraint_adapter import activation_from_bundle

        bundle = ConstraintBundle(
            node_ids=["n-primary", "n-support"],
            constraint_score=0.72,
            relevance=0.81,
            freshness=0.63,
            authority_weight=0.41,
        )
        identity = ConstraintIdentity(
            "bundle:c1", "requester", "may_approve", "expense"
        )
        context = BoundaryContext(
            "run:bounded-1",
            question="approval",
            purpose="bounded-equivalence",
            conditions={"mb_version": "prod", "observation": "selected"},
        )
        activation = activation_from_bundle(
            bundle,
            identity,
            context,
            support=EvidencePolarity.SUPPORT,
        )

        selected_observation = {
            "constraint_id": activation.identity.constraint_id,
            "strength": activation.strength.value,
            "relevance": activation.strength.relevance,
            "freshness": activation.strength.freshness,
            "authority": activation.strength.authority,
            "support": activation.strength.support.value,
            "boundary_id": activation.context.boundary_id,
            "question": activation.context.question,
            "purpose": activation.context.purpose,
        }
        self.assertEqual(
            selected_observation,
            {
                "constraint_id": "bundle:c1",
                "strength": 0.72,
                "relevance": 0.81,
                "freshness": 0.63,
                "authority": 0.41,
                "support": "support",
                "boundary_id": "run:bounded-1",
                "question": "approval",
                "purpose": "bounded-equivalence",
            },
        )

    def test_enterprise_adapter_rejects_missing_observation_instead_of_zero(self):
        from types import SimpleNamespace
        from rdl_enterprise.constraint_adapter import activation_from_bundle

        identity = ConstraintIdentity("c-missing", "s", "rel", "o")
        context = BoundaryContext("b-missing")
        incomplete = SimpleNamespace(
            constraint_score=0.7,
            relevance=0.8,
            # freshness is intentionally absent
            authority_weight=0.4,
        )
        with self.assertRaises(ValueError):
            activation_from_bundle(incomplete, identity, context)

        explicit_unknown = SimpleNamespace(
            constraint_score=0.7,
            relevance=0.8,
            freshness=None,
            authority_weight=0.4,
        )
        with self.assertRaises(ValueError):
            activation_from_bundle(explicit_unknown, identity, context)

    def test_enterprise_bundle_can_be_re_evaluated_by_core_function(self):
        from types import SimpleNamespace
        from rdl_enterprise.constraint_adapter import evaluate_bundle_strength

        bundle = SimpleNamespace(
            constraint_score=0.99,  # legacy score is not trusted as Core input
            relevance=1.0,
            freshness=0.5,
            authority_weight=0.0,
            source_strength=0.5,
            convergence=1.0,
        )
        strength = evaluate_bundle_strength(
            bundle,
            support=EvidencePolarity.OPPOSE,
            weights=ConstraintEvaluationWeights(
                relevance=1.0, freshness=1.0, authority=0.0, source=0.0, convergence=0.0
            ),
        )
        self.assertEqual(strength.value, 0.75)
        self.assertEqual(strength.support, EvidencePolarity.OPPOSE)

    def test_enterprise_bundle_evaluation_record_retains_components(self):
        from types import SimpleNamespace
        from rdl_core import ConstraintEvaluation
        from rdl_enterprise.constraint_adapter import record_bundle_evaluation

        bundle = SimpleNamespace(
            relevance=0.8, freshness=0.6, authority_weight=0.4,
            source_strength=0.5, convergence=0.7,
        )
        evaluation = record_bundle_evaluation(
            bundle,
            BoundaryContext("record-1"),
            provenance=Provenance("bundle-observation"),
        )
        self.assertIsInstance(evaluation, ConstraintEvaluation)
        self.assertEqual(evaluation.source, 0.5)
        self.assertEqual(evaluation.provenance.source, "bundle-observation")
        self.assertEqual(evaluation.evaluator_id, "rdl_enterprise.bundle_constraint")

        with self.assertRaises(ValueError):
            record_bundle_evaluation(bundle, BoundaryContext("record-2"))

    def test_node_description_is_not_a_commitment(self):
        relation = ConstraintIdentity("r1", "node-1", "may_approve", "expense")
        node = NodeDescription(
            "node-1", "finance", relations=(relation,), attributes=(("config", {"mutable": True}),)
        )
        observation = RelationObservation(
            node, relation, BoundaryContext("boundary-1", question="approval"),
            status=RelationObservationStatus.UNRESOLVED,
        )
        self.assertEqual(observation.node.node_id, "node-1")
        self.assertEqual(observation.boundary.question, "approval")
        self.assertIsNone(node.provenance)
        with self.assertRaises(TypeError):
            node.attributes[0][1]["mutable"] = False
        with self.assertRaises(ValueError):
            NodeDescription("", "finance")
        with self.assertRaises(ValueError):
            RelationObservation(
                node,
                ConstraintIdentity("unattached", "s", "r", "o"),
                BoundaryContext("boundary-2"),
                RelationObservationStatus.OBSERVED,
            )
        with self.assertRaises(TypeError):
            RelationObservation(node, relation, BoundaryContext("boundary-3"), status="truth")
        list_node = NodeDescription(
            "node-2", "finance",
            relations=[ConstraintIdentity("r2", "node-2", "may_approve", "expense")],
        )
        self.assertIsInstance(list_node.relations, tuple)
        with self.assertRaises(TypeError):
            BoundaryContext("boundary-4", conditions={"nested": {123: "invalid"}})

        graph = NodeDescriptionGraph((node, list_node))
        self.assertIs(graph.get("node-1"), node)
        external_relation = ConstraintIdentity("external", "node-1", "references", "outside")
        graph_with_external = NodeDescriptionGraph(
            (NodeDescription("node-1", "finance", relations=(external_relation,)),)
        )
        self.assertEqual(graph_with_external.internal_relation_targets, ())
        self.assertEqual(graph_with_external.external_relation_targets, ("outside",))
        self.assertEqual(graph_with_external.classify_target("outside"), RelationTargetScope.UNRESOLVED)
        self.assertEqual(
            graph_with_external.classify_target("outside", ("outside",)),
            RelationTargetScope.EXTERNAL,
        )
        with self.assertRaises(ValueError):
            NodeDescriptionGraph((node, node))

    def test_mbnode_projection_preserves_selected_core_slice(self):
        from rdl_enterprise.mb_graph import MBNode
        from rdl_enterprise.mb_graph_adapter import (
            node_description_from_mbnode,
            relation_observation_from_mbnode,
        )

        relation = ConstraintIdentity("r-mb", "mb-1", "may_approve", "expense")
        node = MBNode(
            id="mb-1",
            domain="finance",
            trigger_pattern={"kind": "approval"},
            action_template={"action": "approve"},
        )
        provenance = Provenance("enterprise:mb_graph", actor="adapter-test")
        description = node_description_from_mbnode(node, (relation,), provenance=provenance)
        observation = relation_observation_from_mbnode(
            node, relation, BoundaryContext("mb-boundary", question="approval"),
            RelationObservationStatus.OBSERVED, provenance=provenance,
        )
        self.assertEqual(description.node_id, "mb-1")
        self.assertEqual(description.relations, (relation,))
        self.assertEqual(observation.boundary.boundary_id, "mb-boundary")
        self.assertIsNone(description.provenance.authority_ref)

    def test_mbnode_relation_edges_and_provenance_are_projected_by_kind(self):
        from rdl_enterprise.mb_graph import MBNode
        from rdl_enterprise.mb_graph_adapter import relation_observations_from_mbnode

        node = MBNode(
            id="mb-edges", domain="finance", trigger_pattern={}, action_template={},
            source_id="policy-1", source_lineage="policy-v1",
            node_relations={"mb-support": "support", "mb-unknown": "unknown"},
        )
        observations = relation_observations_from_mbnode(node, BoundaryContext("edge-b"))
        self.assertEqual(len(observations), 2)
        self.assertEqual(observations[0].status, RelationObservationStatus.OBSERVED)
        self.assertEqual(observations[1].status, RelationObservationStatus.UNRESOLVED)
        self.assertEqual(observations[0].node.provenance.source, "policy-1")
        self.assertEqual(observations[0].node.provenance.lineage, "policy-v1")
        selected_slice = tuple(
            (item.relation.subject, item.relation.relation, item.relation.object, item.status.value)
            for item in observations
        )
        self.assertEqual(
            selected_slice,
            (("mb-edges", "support", "mb-support", "observed"),
             ("mb-edges", "unknown", "mb-unknown", "unresolved")),
        )
        node.source_id = 123
        with self.assertRaises(TypeError):
            relation_observations_from_mbnode(node, BoundaryContext("edge-b"))
        node.source_id = "policy-1"
        node.node_relations["bad"] = "truth"
        with self.assertRaises(ValueError):
            relation_observations_from_mbnode(node, BoundaryContext("edge-b"))

        node.node_relations.pop("bad")
        node.node_relations["mb-independent"] = "independent"
        independent = relation_observations_from_mbnode(node, BoundaryContext("edge-b"))[-1]
        self.assertEqual(independent.status, RelationObservationStatus.OBSERVED)

    def test_mbgraph_projection_returns_core_graph_and_observation_slice(self):
        from types import SimpleNamespace
        from rdl_enterprise.mb_graph import MBNode
        from rdl_enterprise.mb_graph_adapter import project_mbgraph

        node_a = MBNode(
            id="graph-a", domain="finance", trigger_pattern={}, action_template={},
            node_relations={"graph-b": "support"}, source_id="graph-source",
        )
        node_b = MBNode(
            id="graph-b", domain="finance", trigger_pattern={}, action_template={},
        )
        projection = project_mbgraph(
            SimpleNamespace(nodes={"graph-a": node_a, "graph-b": node_b}),
            BoundaryContext("graph-boundary", purpose="projection"),
        )
        self.assertEqual([node.node_id for node in projection.graph.nodes], ["graph-a", "graph-b"])
        self.assertEqual(len(projection.observations), 1)
        self.assertEqual(projection.observations[0].status, RelationObservationStatus.OBSERVED)
        self.assertEqual(projection.observations[0].boundary.purpose, "projection")
        self.assertEqual(
            projection.selected_slice(),
            (
                (("graph-a", "finance"), ("graph-b", "finance")),
                (("graph-a", "support", "graph-b", "observed", "graph-boundary"),),
            ),
        )
        reordered = project_mbgraph(
            SimpleNamespace(nodes={"graph-b": node_b, "graph-a": node_a}),
            BoundaryContext("graph-boundary", purpose="projection"),
        )
        self.assertEqual(projection.selected_slice(), reordered.selected_slice())

        node_a.source_id = "  "
        node_a.source_lineage = None
        blank_source_projection = project_mbgraph(
            SimpleNamespace(nodes={"graph-a": node_a, "graph-b": node_b}),
            BoundaryContext("graph-boundary"),
        )
        self.assertIsNone(blank_source_projection.graph.get("graph-a").provenance)
        with self.assertRaises(ValueError):
            project_mbgraph(
                SimpleNamespace(nodes={"wrong-key": node_a}),
                BoundaryContext("graph-boundary"),
            )

    def test_exact_key_matching_is_an_observation_not_a_commitment(self):
        context = BoundaryContext("trigger-boundary", question="password reset")
        trigger = TriggerDescription(exact_keys=("password", "reset"))
        matched = observe_exact_keys(trigger, "password reset request", context)
        self.assertEqual(matched.status, MatchingObservationStatus.MATCHED)
        self.assertEqual(matched.matched_keys, ("password", "reset"))
        not_matched = observe_exact_keys(trigger, "wifi issue", context)
        self.assertEqual(not_matched.status, MatchingObservationStatus.NOT_MATCHED)
        unresolved = observe_exact_keys(TriggerDescription(), "anything", context)
        self.assertEqual(unresolved.status, MatchingObservationStatus.UNRESOLVED)
        with self.assertRaises(ValueError):
            TriggerDescription(exact_keys=("password", 123))
        normalized = observe_exact_keys(
            TriggerDescription(exact_keys=("Password Reset",)),
            "passwordreset",
            context,
        )
        self.assertEqual(normalized.status, MatchingObservationStatus.MATCHED)
        self.assertEqual(normalized.matched_keys, ("Password Reset",))

        mutable_keys = MatchingObservation(
            trigger=trigger,
            query="password",
            status=MatchingObservationStatus.MATCHED,
            matched_keys=["password"],
            context=context,
        )
        self.assertEqual(mutable_keys.matched_keys, ("password",))

    def test_mbnode_exact_key_trigger_projection_ignores_other_policies(self):
        from rdl_enterprise.mb_graph import MBNode
        from rdl_enterprise.trigger_adapter import matching_observation_from_mbnode

        node = MBNode(
            id="trigger-node", domain="it",
            trigger_pattern={
                "exact_keys": ["password", "reset"],
                "rule_expr": "password.*reset",
                "embedding": [0.1, 0.2],
            },
            action_template={"type": "direct_reply"},
            source_id="trigger-policy",
        )
        observation = matching_observation_from_mbnode(
            node, "password reset request", BoundaryContext("trigger-projection"),
        )
        self.assertEqual(observation.status, MatchingObservationStatus.MATCHED)
        self.assertEqual(observation.matched_keys, ("password", "reset"))
        self.assertEqual(observation.provenance.source, "trigger-policy")
        node.trigger_pattern["exact_keys"] = "password"
        with self.assertRaises(TypeError):
            matching_observation_from_mbnode(
                node, "password", BoundaryContext("trigger-projection-invalid")
            )
