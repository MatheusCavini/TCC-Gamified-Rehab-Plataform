#[cfg(feature = "serde")]
use serde::{Deserialize, Serialize};



#[link(name = "hal_interfaces__rosidl_typesupport_c")]
extern "C" {
    fn rosidl_typesupport_c__get_message_type_support_handle__hal_interfaces__srv__SetString_Request() -> *const std::ffi::c_void;
}

#[link(name = "hal_interfaces__rosidl_generator_c")]
extern "C" {
    fn hal_interfaces__srv__SetString_Request__init(msg: *mut SetString_Request) -> bool;
    fn hal_interfaces__srv__SetString_Request__Sequence__init(seq: *mut rosidl_runtime_rs::Sequence<SetString_Request>, size: usize) -> bool;
    fn hal_interfaces__srv__SetString_Request__Sequence__fini(seq: *mut rosidl_runtime_rs::Sequence<SetString_Request>);
    fn hal_interfaces__srv__SetString_Request__Sequence__copy(in_seq: &rosidl_runtime_rs::Sequence<SetString_Request>, out_seq: *mut rosidl_runtime_rs::Sequence<SetString_Request>) -> bool;
}

// Corresponds to hal_interfaces__srv__SetString_Request
#[cfg_attr(feature = "serde", derive(Deserialize, Serialize))]


// This struct is not documented.
#[allow(missing_docs)]

#[allow(non_camel_case_types)]
#[repr(C)]
#[derive(Clone, Debug, PartialEq, PartialOrd)]
pub struct SetString_Request {

    // This member is not documented.
    #[allow(missing_docs)]
    pub data: rosidl_runtime_rs::String,

}



impl Default for SetString_Request {
  fn default() -> Self {
    unsafe {
      let mut msg = std::mem::zeroed();
      if !hal_interfaces__srv__SetString_Request__init(&mut msg as *mut _) {
        panic!("Call to hal_interfaces__srv__SetString_Request__init() failed");
      }
      msg
    }
  }
}

impl rosidl_runtime_rs::SequenceAlloc for SetString_Request {
  fn sequence_init(seq: &mut rosidl_runtime_rs::Sequence<Self>, size: usize) -> bool {
    // SAFETY: This is safe since the pointer is guaranteed to be valid/initialized.
    unsafe { hal_interfaces__srv__SetString_Request__Sequence__init(seq as *mut _, size) }
  }
  fn sequence_fini(seq: &mut rosidl_runtime_rs::Sequence<Self>) {
    // SAFETY: This is safe since the pointer is guaranteed to be valid/initialized.
    unsafe { hal_interfaces__srv__SetString_Request__Sequence__fini(seq as *mut _) }
  }
  fn sequence_copy(in_seq: &rosidl_runtime_rs::Sequence<Self>, out_seq: &mut rosidl_runtime_rs::Sequence<Self>) -> bool {
    // SAFETY: This is safe since the pointer is guaranteed to be valid/initialized.
    unsafe { hal_interfaces__srv__SetString_Request__Sequence__copy(in_seq, out_seq as *mut _) }
  }
}

impl rosidl_runtime_rs::Message for SetString_Request {
  type RmwMsg = Self;
  fn into_rmw_message(msg_cow: std::borrow::Cow<'_, Self>) -> std::borrow::Cow<'_, Self::RmwMsg> { msg_cow }
  fn from_rmw_message(msg: Self::RmwMsg) -> Self { msg }
}

impl rosidl_runtime_rs::RmwMessage for SetString_Request where Self: Sized {
  const TYPE_NAME: &'static str = "hal_interfaces/srv/SetString_Request";
  fn get_type_support() -> *const std::ffi::c_void {
    // SAFETY: No preconditions for this function.
    unsafe { rosidl_typesupport_c__get_message_type_support_handle__hal_interfaces__srv__SetString_Request() }
  }
}


#[link(name = "hal_interfaces__rosidl_typesupport_c")]
extern "C" {
    fn rosidl_typesupport_c__get_message_type_support_handle__hal_interfaces__srv__SetString_Response() -> *const std::ffi::c_void;
}

#[link(name = "hal_interfaces__rosidl_generator_c")]
extern "C" {
    fn hal_interfaces__srv__SetString_Response__init(msg: *mut SetString_Response) -> bool;
    fn hal_interfaces__srv__SetString_Response__Sequence__init(seq: *mut rosidl_runtime_rs::Sequence<SetString_Response>, size: usize) -> bool;
    fn hal_interfaces__srv__SetString_Response__Sequence__fini(seq: *mut rosidl_runtime_rs::Sequence<SetString_Response>);
    fn hal_interfaces__srv__SetString_Response__Sequence__copy(in_seq: &rosidl_runtime_rs::Sequence<SetString_Response>, out_seq: *mut rosidl_runtime_rs::Sequence<SetString_Response>) -> bool;
}

// Corresponds to hal_interfaces__srv__SetString_Response
#[cfg_attr(feature = "serde", derive(Deserialize, Serialize))]


// This struct is not documented.
#[allow(missing_docs)]

#[allow(non_camel_case_types)]
#[repr(C)]
#[derive(Clone, Debug, PartialEq, PartialOrd)]
pub struct SetString_Response {

    // This member is not documented.
    #[allow(missing_docs)]
    pub success: bool,


    // This member is not documented.
    #[allow(missing_docs)]
    pub message: rosidl_runtime_rs::String,

}



impl Default for SetString_Response {
  fn default() -> Self {
    unsafe {
      let mut msg = std::mem::zeroed();
      if !hal_interfaces__srv__SetString_Response__init(&mut msg as *mut _) {
        panic!("Call to hal_interfaces__srv__SetString_Response__init() failed");
      }
      msg
    }
  }
}

impl rosidl_runtime_rs::SequenceAlloc for SetString_Response {
  fn sequence_init(seq: &mut rosidl_runtime_rs::Sequence<Self>, size: usize) -> bool {
    // SAFETY: This is safe since the pointer is guaranteed to be valid/initialized.
    unsafe { hal_interfaces__srv__SetString_Response__Sequence__init(seq as *mut _, size) }
  }
  fn sequence_fini(seq: &mut rosidl_runtime_rs::Sequence<Self>) {
    // SAFETY: This is safe since the pointer is guaranteed to be valid/initialized.
    unsafe { hal_interfaces__srv__SetString_Response__Sequence__fini(seq as *mut _) }
  }
  fn sequence_copy(in_seq: &rosidl_runtime_rs::Sequence<Self>, out_seq: &mut rosidl_runtime_rs::Sequence<Self>) -> bool {
    // SAFETY: This is safe since the pointer is guaranteed to be valid/initialized.
    unsafe { hal_interfaces__srv__SetString_Response__Sequence__copy(in_seq, out_seq as *mut _) }
  }
}

impl rosidl_runtime_rs::Message for SetString_Response {
  type RmwMsg = Self;
  fn into_rmw_message(msg_cow: std::borrow::Cow<'_, Self>) -> std::borrow::Cow<'_, Self::RmwMsg> { msg_cow }
  fn from_rmw_message(msg: Self::RmwMsg) -> Self { msg }
}

impl rosidl_runtime_rs::RmwMessage for SetString_Response where Self: Sized {
  const TYPE_NAME: &'static str = "hal_interfaces/srv/SetString_Response";
  fn get_type_support() -> *const std::ffi::c_void {
    // SAFETY: No preconditions for this function.
    unsafe { rosidl_typesupport_c__get_message_type_support_handle__hal_interfaces__srv__SetString_Response() }
  }
}






#[link(name = "hal_interfaces__rosidl_typesupport_c")]
extern "C" {
    fn rosidl_typesupport_c__get_service_type_support_handle__hal_interfaces__srv__SetString() -> *const std::ffi::c_void;
}

// Corresponds to hal_interfaces__srv__SetString
#[allow(missing_docs, non_camel_case_types)]
pub struct SetString;

impl rosidl_runtime_rs::Service for SetString {
    type Request = SetString_Request;
    type Response = SetString_Response;

    fn get_type_support() -> *const std::ffi::c_void {
        // SAFETY: No preconditions for this function.
        unsafe { rosidl_typesupport_c__get_service_type_support_handle__hal_interfaces__srv__SetString() }
    }
}


