// generated from rosidl_generator_cpp/resource/idl__builder.hpp.em
// with input from hal_interfaces:srv/SetString.idl
// generated code does not contain a copyright notice

#ifndef HAL_INTERFACES__SRV__DETAIL__SET_STRING__BUILDER_HPP_
#define HAL_INTERFACES__SRV__DETAIL__SET_STRING__BUILDER_HPP_

#include <algorithm>
#include <utility>

#include "hal_interfaces/srv/detail/set_string__struct.hpp"
#include "rosidl_runtime_cpp/message_initialization.hpp"


namespace hal_interfaces
{

namespace srv
{

namespace builder
{

class Init_SetString_Request_data
{
public:
  Init_SetString_Request_data()
  : msg_(::rosidl_runtime_cpp::MessageInitialization::SKIP)
  {}
  ::hal_interfaces::srv::SetString_Request data(::hal_interfaces::srv::SetString_Request::_data_type arg)
  {
    msg_.data = std::move(arg);
    return std::move(msg_);
  }

private:
  ::hal_interfaces::srv::SetString_Request msg_;
};

}  // namespace builder

}  // namespace srv

template<typename MessageType>
auto build();

template<>
inline
auto build<::hal_interfaces::srv::SetString_Request>()
{
  return hal_interfaces::srv::builder::Init_SetString_Request_data();
}

}  // namespace hal_interfaces


namespace hal_interfaces
{

namespace srv
{

namespace builder
{

class Init_SetString_Response_message
{
public:
  explicit Init_SetString_Response_message(::hal_interfaces::srv::SetString_Response & msg)
  : msg_(msg)
  {}
  ::hal_interfaces::srv::SetString_Response message(::hal_interfaces::srv::SetString_Response::_message_type arg)
  {
    msg_.message = std::move(arg);
    return std::move(msg_);
  }

private:
  ::hal_interfaces::srv::SetString_Response msg_;
};

class Init_SetString_Response_success
{
public:
  Init_SetString_Response_success()
  : msg_(::rosidl_runtime_cpp::MessageInitialization::SKIP)
  {}
  Init_SetString_Response_message success(::hal_interfaces::srv::SetString_Response::_success_type arg)
  {
    msg_.success = std::move(arg);
    return Init_SetString_Response_message(msg_);
  }

private:
  ::hal_interfaces::srv::SetString_Response msg_;
};

}  // namespace builder

}  // namespace srv

template<typename MessageType>
auto build();

template<>
inline
auto build<::hal_interfaces::srv::SetString_Response>()
{
  return hal_interfaces::srv::builder::Init_SetString_Response_success();
}

}  // namespace hal_interfaces

#endif  // HAL_INTERFACES__SRV__DETAIL__SET_STRING__BUILDER_HPP_
