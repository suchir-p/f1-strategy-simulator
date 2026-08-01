cmake_minimum_required(VERSION 3.20)
# One version for the whole project, kept in the repository's VERSION file.
set(F1SIM_VERSION_FILE "${CMAKE_CURRENT_SOURCE_DIR}/../VERSION")
if(EXISTS "${F1SIM_VERSION_FILE}")
  file(STRINGS "${F1SIM_VERSION_FILE}" F1SIM_PROJECT_VERSION LIMIT_COUNT 1)
else()
  set(F1SIM_PROJECT_VERSION "0.0.0")
endif()
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${F1SIM_VERSION_FILE}")
project(f1sim_engine VERSION ${F1SIM_PROJECT_VERSION} LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

option(F1SIM_BUILD_TESTS "Build GoogleTest unit tests" ON)
option(F1SIM_BUILD_PYTHON "Build pybind11 Python module" OFF)
option(F1SIM_SANITIZE "Build with address/undefined sanitizers" OFF)

find_package(OpenMP REQUIRED)

include(FetchContent)
FetchContent_Declare(
  nlohmann_json
  GIT_REPOSITORY https://github.com/nlohmann/json.git
  GIT_TAG v3.11.3
  GIT_SHALLOW TRUE)
FetchContent_MakeAvailable(nlohmann_json)

# ---- core library -----------------------------------------------------------
add_library(f1sim
  src/rng.cpp
  src/strategy.cpp
  src/simulator.cpp
  src/monte_carlo.cpp
  src/optimizer.cpp
  src/replay.cpp
  src/config_io.cpp)
target_include_directories(f1sim PUBLIC include)
target_link_libraries(f1sim PUBLIC OpenMP::OpenMP_CXX nlohmann_json::nlohmann_json)
target_compile_options(f1sim PRIVATE -Wall -Wextra -Wpedantic)

if(F1SIM_SANITIZE)
  target_compile_options(f1sim PUBLIC -fsanitize=address,undefined -fno-omit-frame-pointer)
  target_link_options(f1sim PUBLIC -fsanitize=address,undefined)
endif()

# ---- command-line harness ---------------------------------------------------
add_executable(f1sim_cli tools/cli.cpp)
target_link_libraries(f1sim_cli PRIVATE f1sim)

# ---- benchmark --------------------------------------------------------------
add_executable(f1sim_bench tools/bench.cpp)
target_link_libraries(f1sim_bench PRIVATE f1sim)
string(TOUPPER "${CMAKE_BUILD_TYPE}" F1SIM_BUILD_TYPE_UPPER)
target_compile_definitions(f1sim_bench PRIVATE
  F1SIM_VERSION="${PROJECT_VERSION}"
  F1SIM_CXX_FLAGS="${CMAKE_BUILD_TYPE}: ${CMAKE_CXX_FLAGS} ${CMAKE_CXX_FLAGS_${F1SIM_BUILD_TYPE_UPPER}}")

# ---- tests ------------------------------------------------------------------
if(F1SIM_BUILD_TESTS)
  enable_testing()
  FetchContent_Declare(
    googletest
    GIT_REPOSITORY https://github.com/google/googletest.git
    GIT_TAG v1.15.2
    GIT_SHALLOW TRUE)
  set(INSTALL_GTEST OFF CACHE BOOL "" FORCE)
  FetchContent_MakeAvailable(googletest)

  add_executable(f1sim_tests
    tests/test_rng.cpp
    tests/test_strategy.cpp
    tests/test_simulator.cpp
    tests/test_monte_carlo.cpp
    tests/test_optimizer.cpp
    tests/test_replay.cpp)
  target_link_libraries(f1sim_tests PRIVATE f1sim GTest::gtest_main)
  target_compile_definitions(f1sim_tests PRIVATE
    F1SIM_EXAMPLE_CONFIG="${CMAKE_CURRENT_SOURCE_DIR}/../data/examples/synthetic_race.json")
  include(GoogleTest)
  gtest_discover_tests(f1sim_tests)
endif()

# ---- python bindings --------------------------------------------------------
if(F1SIM_BUILD_PYTHON)
  find_package(Python COMPONENTS Interpreter Development.Module REQUIRED)
  find_package(pybind11 CONFIG REQUIRED)
  pybind11_add_module(f1sim_py bindings/python_bindings.cpp)
  set_target_properties(f1sim_py PROPERTIES OUTPUT_NAME f1sim)
  target_link_libraries(f1sim_py PRIVATE f1sim)
  target_compile_definitions(f1sim_py PRIVATE F1SIM_VERSION="${PROJECT_VERSION}")
endif()
