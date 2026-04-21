from sub_agent_runtime.prompting.failures import classify_write_failure as _classify_write_failure

def test_classify_build123d_rectangle_centered_keyword_as_api_failure() -> None:
    assert (
        _classify_write_failure(
            tool_name="execute_build123d",
            error_text="Exit code: 1",
            stderr_text=(
                "TypeError: Rectangle.__init__() got an unexpected keyword argument 'centered'"
            ),
        )
        == "execute_build123d_api_lint_failure"
    )


def test_classify_build123d_cylinder_axis_keyword_as_api_failure() -> None:
    assert (
        _classify_write_failure(
            tool_name="execute_build123d",
            error_text="Exit code: 1",
            stderr_text=(
                "TypeError: Cylinder.__init__() got an unexpected keyword argument 'axis'"
            ),
        )
        == "execute_build123d_api_lint_failure"
    )


def test_classify_build123d_indentation_error_as_python_syntax_failure() -> None:
    assert (
        _classify_write_failure(
            tool_name="execute_build123d",
            error_text='Exit code: 1 | File "/app/aicad_runtime_main.py", line 59',
            stderr_text="IndentationError: unindent does not match any outer indentation level",
        )
        == "execute_build123d_python_syntax_failure"
    )


def test_classify_build123d_method_minus_cylinder_as_boolean_shape_api_failure() -> None:
    assert (
        _classify_write_failure(
            tool_name="execute_build123d",
            error_text="Exit code: 1",
            stderr_text=(
                "TypeError: unsupported operand type(s) for -: 'method' and 'Cylinder'"
            ),
        )
        == "execute_build123d_boolean_shape_api_failure"
    )


def test_classify_build123d_broad_fillet_runtime_error_as_selector_failure() -> None:
    assert (
        _classify_write_failure(
            tool_name="execute_build123d",
            error_text="Exit code: 1",
            stderr_text=(
                "OCP.Standard.Standard_Failure: There are no suitable edges for chamfer or fillet"
            ),
        )
        == "execute_build123d_selector_failure"
    )


def test_classify_build123d_fillet_not_done_runtime_error_as_selector_failure() -> None:
    assert (
        _classify_write_failure(
            tool_name="execute_build123d",
            error_text="Exit code: 1",
            stderr_text=(
                "OCP.StdFail.StdFail_NotDone: BRep_API: command not done\n"
                "Traceback (most recent call last):\n"
                "  File \"/app/aicad_runtime_main.py\", line 183, in <module>\n"
                "    base_shell = fillet(base_shell.edges(), 3.0)\n"
            ),
        )
        == "execute_build123d_selector_failure"
    )


def test_classify_build123d_detached_subtractive_builder_runtime_error() -> None:
    assert (
        _classify_write_failure(
            tool_name="execute_build123d",
            error_text="Exit code: 1",
            stderr_text="RuntimeError: Nothing to subtract from",
        )
        == "execute_build123d_detached_subtractive_builder_failure"
    )
