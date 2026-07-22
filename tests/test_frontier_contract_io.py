import copy
import json
import re
import tempfile
import unittest
from pathlib import Path

from prompt_performance_engine.contracts import PACKAGE_ROOT
from prompt_performance_engine.frontier_contracts import (
    MAX_CAMPAIGN_FILE_BYTES,
    load_frontier_campaign_bundle,
    validate_frontier_campaign_plan,
    validate_frontier_release_policy,
    validate_frontier_source_commitment,
)
from prompt_performance_engine.frontier_io import (
    ArtifactFormatError,
    ArtifactPathError,
    canonical_authority_bytes,
)
from prompt_performance_engine.hashing import hash_payload
from tests.test_frontier_preflight import build_fixture, rehash_plan, write_json


def read_object(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def path_slots(value: object, location: tuple[object, ...] = ()):
    if isinstance(value, dict):
        for key, child in value.items():
            child_location = (*location, key)
            if key.endswith("_path"):
                yield child_location
            yield from path_slots(child, child_location)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from path_slots(child, (*location, index))


def set_location(value: object, location: tuple[object, ...], replacement: object) -> None:
    cursor = value
    for part in location[:-1]:
        cursor = cursor[part]  # type: ignore[index]
    cursor[location[-1]] = replacement  # type: ignore[index]


class FrontierContractPathValidationTests(unittest.TestCase):
    def test_standalone_validators_and_schemas_reject_path_aliases(self) -> None:
        invalid_paths = (
            "/absolute.json",
            "../outside.json",
            "nested/../outside.json",
            "nested//file.json",
            "nested/./file.json",
            r"nested\file.json",
            "C:drive-relative.json",
            " leading.json",
            "trailing.json ",
        )
        schema_specs = (
            (
                "frontier-campaign-plan.schema.json",
                validate_frontier_campaign_plan,
                lambda value, path: value.__setitem__("release_policy_path", path),
                "campaign_sha256",
            ),
            (
                "frontier-release-policy.schema.json",
                validate_frontier_release_policy,
                lambda value, path: value["statistical_design"].__setitem__(
                    "stopping_rule_path", path
                ),
                "policy_sha256",
            ),
            (
                "frontier-source-commitment.schema.json",
                validate_frontier_source_commitment,
                lambda value, path: value.__setitem__("license_review_path", path),
                "commitment_sha256",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan_path = build_fixture(root)
            source_values = {
                "frontier-campaign-plan.schema.json": read_object(plan_path),
                "frontier-release-policy.schema.json": read_object(
                    root / "release-policy.json"
                ),
                "frontier-source-commitment.schema.json": read_object(
                    root / "source-commitment.json"
                ),
            }
            for schema_name, validator, mutate, hash_field in schema_specs:
                schema = read_object(PACKAGE_ROOT / "schemas" / schema_name)
                pattern = re.compile(schema["$defs"]["relativePath"]["pattern"])
                for invalid_path in invalid_paths:
                    with self.subTest(schema=schema_name, path=invalid_path):
                        value = copy.deepcopy(source_values[schema_name])
                        mutate(value, invalid_path)
                        value[hash_field] = hash_payload(value, hash_field)
                        self.assertTrue(validator(value))
                        self.assertIsNone(pattern.fullmatch(invalid_path))

    def test_every_standalone_path_field_uses_portable_path_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan_path = build_fixture(root)
            specifications = (
                (
                    read_object(plan_path),
                    validate_frontier_campaign_plan,
                    "campaign_sha256",
                ),
                (
                    read_object(root / "release-policy.json"),
                    validate_frontier_release_policy,
                    "policy_sha256",
                ),
                (
                    read_object(root / "source-commitment.json"),
                    validate_frontier_source_commitment,
                    "commitment_sha256",
                ),
            )
            for original, validator, hash_field in specifications:
                slots = tuple(path_slots(original))
                self.assertTrue(slots)
                for slot in slots:
                    with self.subTest(hash_field=hash_field, slot=slot):
                        value = copy.deepcopy(original)
                        set_location(value, slot, "nested/../forbidden.json")
                        value[hash_field] = hash_payload(value, hash_field)
                        failures = validator(value)
                        self.assertTrue(
                            any(
                                "path" in failure and "invalid" in failure
                                for failure in failures
                            ),
                            failures,
                        )

    def test_standalone_plan_validator_rejects_non_nfc_and_device_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = read_object(build_fixture(root))
            for invalid_path in (
                "caf\u0065\u0301.json",
                "COM\u00b9.json",
                "LPT\u00b3.txt",
                "CONIN$",
                "conout$.json",
            ):
                with self.subTest(path=invalid_path):
                    mutated = copy.deepcopy(plan)
                    mutated["release_policy_path"] = invalid_path
                    rehash_plan(mutated)
                    self.assertTrue(validate_frontier_campaign_plan(mutated))


class FrontierCampaignBundleIOTests(unittest.TestCase):
    def test_authority_json_must_already_be_canonical(self) -> None:
        for authority_file in (
            "campaign-plan.json",
            "release-policy.json",
            "source-commitment.json",
        ):
            with (
                self.subTest(authority_file=authority_file),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                plan_path = build_fixture(root)
                target = root / authority_file
                target.write_text(
                    json.dumps(read_object(target), ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                with self.assertRaises(ArtifactFormatError):
                    load_frontier_campaign_bundle(plan_path, root=root)

    def test_root_internal_file_and_parent_symlinks_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan_path = build_fixture(root)
            policy_path = root / "release-policy.json"
            real_policy = root / "real-release-policy.json"
            real_policy.write_bytes(policy_path.read_bytes())
            policy_path.unlink()
            try:
                policy_path.symlink_to(real_policy)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"symbolic links unavailable: {type(error).__name__}")
            with self.assertRaises(ArtifactPathError):
                load_frontier_campaign_bundle(plan_path, root=root)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan_path = build_fixture(root)
            plan = read_object(plan_path)
            candidate_path = root / plan["candidate_artifacts"]["package_path"]
            alias = root / "candidate-alias"
            try:
                alias.symlink_to(candidate_path.parent, target_is_directory=True)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"symbolic links unavailable: {type(error).__name__}")
            plan["candidate_artifacts"]["package_path"] = (
                f"candidate-alias/{candidate_path.name}"
            )
            rehash_plan(plan)
            write_json(plan_path, plan)
            with self.assertRaises(ArtifactPathError):
                load_frontier_campaign_bundle(plan_path, root=root)

    def test_storage_junction_or_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan_path = build_fixture(root)
            storage = root / "storage"
            real_storage = root / "real-storage"
            storage.rename(real_storage)
            try:
                storage.symlink_to(real_storage, target_is_directory=True)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"symbolic links unavailable: {type(error).__name__}")
            with self.assertRaises(ArtifactPathError):
                load_frontier_campaign_bundle(plan_path, root=root)

    def test_every_referenced_file_read_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan_path = build_fixture(root)
            plan = read_object(plan_path)
            candidate_path = root / plan["candidate_artifacts"]["package_path"]
            with candidate_path.open("wb") as stream:
                stream.truncate(MAX_CAMPAIGN_FILE_BYTES + 1)
            with self.assertRaises(ArtifactFormatError):
                load_frontier_campaign_bundle(plan_path, root=root)

    def test_input_plan_symlink_and_noncanonical_relative_alias_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan_path = build_fixture(root)
            alias = root / "plan-alias.json"
            try:
                alias.symlink_to(plan_path)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"symbolic links unavailable: {type(error).__name__}")
            with self.assertRaises(ArtifactPathError):
                load_frontier_campaign_bundle(alias, root=root)
            with self.assertRaises(ValueError):
                load_frontier_campaign_bundle(Path("nested/../campaign-plan.json"), root=root)

    def test_symlink_campaign_root_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as parent:
            root = Path(directory)
            plan_path = build_fixture(root)
            linked_root = Path(parent) / "linked-campaign"
            try:
                linked_root.symlink_to(root, target_is_directory=True)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"symbolic links unavailable: {type(error).__name__}")
            with self.assertRaises(ValueError):
                load_frontier_campaign_bundle(plan_path, root=linked_root)

    def test_canonical_fixture_still_loads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan_path = build_fixture(root)
            self.assertEqual(
                plan_path.read_bytes(),
                canonical_authority_bytes(read_object(plan_path)),
            )
            bundle = load_frontier_campaign_bundle(plan_path, root=root)
            self.assertEqual(bundle.root, root.resolve())


if __name__ == "__main__":
    unittest.main()
