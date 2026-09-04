# generated from rosidl_cmake/cmake/rosidl_cmake_aggregate_target-extras.cmake.in

# Create a convenience aggregate target hal_interfaces::hal_interfaces
# that links all generated interface targets, so downstream packages can use
# a single modern CMake target name instead of ${hal_interfaces_TARGETS}.
if(hal_interfaces_TARGETS AND NOT TARGET hal_interfaces::hal_interfaces)
  add_library(hal_interfaces::hal_interfaces INTERFACE IMPORTED)
  set_target_properties(hal_interfaces::hal_interfaces PROPERTIES
    INTERFACE_LINK_LIBRARIES "${hal_interfaces_TARGETS}")
endif()
