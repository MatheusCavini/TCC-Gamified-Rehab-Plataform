// generated from rosidl_generator_cpp/resource/idl__traits.hpp.em
// with input from hal_interfaces:srv/SetString.idl
// generated code does not contain a copyright notice

#ifndef HAL_INTERFACES__SRV__DETAIL__SET_STRING__TRAITS_HPP_
#define HAL_INTERFACES__SRV__DETAIL__SET_STRING__TRAITS_HPP_

#include <stdint.h>

#include <sstream>
#include <string>
#include <type_traits>

#include "hal_interfaces/srv/detail/set_string__struct.hpp"
#include "rosidl_runtime_cpp/traits.hpp"

namespace hal_interfaces
{

namespace srv
{

inline void to_flow_style_yaml(
  const SetString_Request & msg,
  std::ostream & out)
{
  out << "{";
  // member: data
  {
    out << "data: ";
    rosidl_generator_traits::value_to_yaml(msg.data, out);
  }
  out << "}";
}  // NOLINT(readability/fn_size)

inline void to_block_style_yaml(
  const SetString_Request & msg,
  std::ostream & out, size_t indentation = 0)
{
  // member: data
  {
    if (indentation > 0) {
      out << std::string(indentation, ' ');
    }
    out << "data: ";
    rosidl_generator_traits::value_to_yaml(msg.data, out);
    out << "\n";
  }
}  // NOLINT(readability/fn_size)

inline std::string to_yaml(const SetString_Request & msg, bool use_flow_style = false)
{
  std::ostringstream out;
  if (use_flow_style) {
    to_flow_style_yaml(msg, out);
  } else {
    to_block_style_yaml(msg, out);
  }
  return out.str();
}

}  // namespace srv

}  // namespace hal_interfaces

namespace rosidl_generator_traits
{

[[deprecated("use hal_interfaces::srv::to_block_style_yaml() instead")]]
inline void to_yaml(
  const hal_interfaces::srv::SetString_Request & msg,
  std::ostream & out, size_t indentation = 0)
{
  hal_interfaces::srv::to_block_style_yaml(msg, out, indentation);
}

[[deprecated("use hal_interfaces::srv::to_yaml() instead")]]
inline std::string to_yaml(const hal_interfaces::srv::SetString_Request & msg)
{
  return hal_interfaces::srv::to_yaml(msg);
}

template<>
inline const char * data_type<hal_interfaces::srv::SetString_Request>()
{
  return "hal_interfaces::srv::SetString_Request";
}

template<>
inline const char * name<hal_interfaces::srv::SetString_Request>()
{
  return "hal_interfaces/srv/SetString_Request";
}

template<>
struct has_fixed_size<hal_interfaces::srv::SetString_Request>
  : std::integral_constant<bool, false> {};

template<>
struct has_bounded_size<hal_interfaces::srv::SetString_Request>
  : std::integral_constant<bool, false> {};

template<>
struct is_message<hal_interfaces::srv::SetString_Request>
  : std::true_type {};

}  // namespace rosidl_generator_traits

namespace hal_interfaces
{

namespace srv
{

inline void to_flow_style_yaml(
  const SetString_Response & msg,
  std::ostream & out)
{
  out << "{";
  // member: success
  {
    out << "success: ";
    rosidl_generator_traits::value_to_yaml(msg.success, out);
    out << ", ";
  }

  // member: message
  {
    out << "message: ";
    rosidl_generator_traits::value_to_yaml(msg.message, out);
  }
  out << "}";
}  // NOLINT(readability/fn_size)

inline void to_block_style_yaml(
  const SetString_Response & msg,
  std::ostream & out, size_t indentation = 0)
{
  // member: success
  {
    if (indentation > 0) {
      out << std::string(indentation, ' ');
    }
    out << "success: ";
    rosidl_generator_traits::value_to_yaml(msg.success, out);
    out << "\n";
  }

  // member: message
  {
    if (indentation > 0) {
      out << std::string(indentation, ' ');
    }
    out << "message: ";
    rosidl_generator_traits::value_to_yaml(msg.message, out);
    out << "\n";
  }
}  // NOLINT(readability/fn_size)

inline std::string to_yaml(const SetString_Response & msg, bool use_flow_style = false)
{
  std::ostringstream out;
  if (use_flow_style) {
    to_flow_style_yaml(msg, out);
  } else {
    to_block_style_yaml(msg, out);
  }
  return out.str();
}

}  // namespace srv

}  // namespace hal_interfaces

namespace rosidl_generator_traits
{

[[deprecated("use hal_interfaces::srv::to_block_style_yaml() instead")]]
inline void to_yaml(
  const hal_interfaces::srv::SetString_Response & msg,
  std::ostream & out, size_t indentation = 0)
{
  hal_interfaces::srv::to_block_style_yaml(msg, out, indentation);
}

[[deprecated("use hal_interfaces::srv::to_yaml() instead")]]
inline std::string to_yaml(const hal_interfaces::srv::SetString_Response & msg)
{
  return hal_interfaces::srv::to_yaml(msg);
}

template<>
inline const char * data_type<hal_interfaces::srv::SetString_Response>()
{
  return "hal_interfaces::srv::SetString_Response";
}

template<>
inline const char * name<hal_interfaces::srv::SetString_Response>()
{
  return "hal_interfaces/srv/SetString_Response";
}

template<>
struct has_fixed_size<hal_interfaces::srv::SetString_Response>
  : std::integral_constant<bool, false> {};

template<>
struct has_bounded_size<hal_interfaces::srv::SetString_Response>
  : std::integral_constant<bool, false> {};

template<>
struct is_message<hal_interfaces::srv::SetString_Response>
  : std::true_type {};

}  // namespace rosidl_generator_traits

namespace rosidl_generator_traits
{

template<>
inline const char * data_type<hal_interfaces::srv::SetString>()
{
  return "hal_interfaces::srv::SetString";
}

template<>
inline const char * name<hal_interfaces::srv::SetString>()
{
  return "hal_interfaces/srv/SetString";
}

template<>
struct has_fixed_size<hal_interfaces::srv::SetString>
  : std::integral_constant<
    bool,
    has_fixed_size<hal_interfaces::srv::SetString_Request>::value &&
    has_fixed_size<hal_interfaces::srv::SetString_Response>::value
  >
{
};

template<>
struct has_bounded_size<hal_interfaces::srv::SetString>
  : std::integral_constant<
    bool,
    has_bounded_size<hal_interfaces::srv::SetString_Request>::value &&
    has_bounded_size<hal_interfaces::srv::SetString_Response>::value
  >
{
};

template<>
struct is_service<hal_interfaces::srv::SetString>
  : std::true_type
{
};

template<>
struct is_service_request<hal_interfaces::srv::SetString_Request>
  : std::true_type
{
};

template<>
struct is_service_response<hal_interfaces::srv::SetString_Response>
  : std::true_type
{
};

}  // namespace rosidl_generator_traits

#endif  // HAL_INTERFACES__SRV__DETAIL__SET_STRING__TRAITS_HPP_
