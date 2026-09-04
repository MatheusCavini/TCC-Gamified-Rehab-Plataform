// **Impedance Control v5 - versão "sensor + atuador"**
// Desenvolvido originalmente por: Lucas Cardoso
// Reescrito em: 22/08/2026
//
// NESTA VERSÃO o Arduino é responsável APENAS por:
//   1. Ler a posição do encoder (via biblioteca Encoder.h, como em New_controlv5.ino)
//   2. Ler as 6 células de carga conectadas em A0..A5 (leitura rápida via acesso
//      direto aos registradores do ADC - mesmo método do Impedance_Control_v5 original)
//   3. Enviar essas leituras ao computador via SerialUSB
//   4. Receber do computador o sinal de controle (saída do PID já calculado no PC)
//   5. Aplicar esse sinal na ponte H (IBT_2)
//
// TODO o cálculo de controle (PID / impedância) foi movido para o script Python
// (controller.py), que roda no computador. O Arduino não decide mais nada sobre
// o controle - ele só mede e atua.
//
// Bibliotecas necessárias (Arduino IDE > Sketch > Include Library > Manage Libraries):
//   - "Encoder" de Paul Stoffregen

#include "Arduino.h"
#include "wiring_private.h"
#include <Encoder.h>

//################## Pinos ##################
// Encoder (mesmos pinos usados em New_controlv5.ino)
#define chA 6
#define chB 10

// Ativação do motor por PWM (PONTE H: IBT_2)
#define pinCONTROL_RH 8
#define pinCONTROL_LH 9

//################## Objetos ##################
Encoder myEnc(chA, chB);

//################## Variáveis ##################
long encoderPos = 0;

uint32_t S1 = 0, S2 = 0, S3 = 0, S4 = 0, S5 = 0, S6 = 0;

unsigned long tempoInicio = 0;
unsigned long tempoAtual = 0;

char serialFlag = '0';   // '0' = parado / aguardando início, '1' = rodando
char lineBuffer[64];
double controlOutput = 0;  // valor recebido do PC (sinal assinado -> indica sentido)

const unsigned long RESPONSE_TIMEOUT_MS = 50;  // tempo máx. de espera pela resposta do PC

//##############################################################################
// Leitura rápida do ADC por acesso direto aos registradores (mesmo método
// utilizado no Impedance_Control_v5 original, para as células de carga em A0..A5)
//##############################################################################
static __inline__ void ADCsync() __attribute__((always_inline, unused));
static void ADCsync() {
  while (ADC->STATUS.bit.SYNCBUSY == 1);  // espera o ADC ficar livre
}

uint32_t anaRead(uint32_t ulPin) {
  ADCsync();
  ADC->INPUTCTRL.bit.MUXPOS = g_APinDescription[ulPin].ulADCChannelNumber;  // seleciona entrada
  ADCsync();
  ADC->CTRLA.bit.ENABLE = 0x01;              // habilita ADC
  ADC->INTFLAG.bit.RESRDY = 1;               // limpa flag de dado pronto
  ADCsync();
  ADC->SWTRIG.bit.START = 1;                 // inicia conversão
  while (ADC->INTFLAG.bit.RESRDY == 0);      // espera terminar
  ADCsync();
  uint32_t valueRead = ADC->RESULT.reg;
  ADCsync();
  ADC->CTRLA.bit.ENABLE = 0x00;              // desabilita ADC
  ADCsync();
  ADC->SWTRIG.reg = 0x01;                    // flush
  return valueRead;
}

void setup() {
  SerialUSB.begin(2000000);              // inicia a comunicação serial
  SerialUSB.setTimeout(RESPONSE_TIMEOUT_MS);

  //###################################################################################
  // ADC setup (mesmo do Impedance_Control_v5 original)
  //###################################################################################
  ADCsync();
  ADC->INPUTCTRL.bit.GAIN = ADC_INPUTCTRL_GAIN_DIV2_Val;     // ganho 2X
  ADCsync();
  ADC->REFCTRL.bit.REFSEL = ADC_REFCTRL_REFSEL_INTVCC1_Val;  // 1/2 VDDANA

  ADCsync();
  ADC->AVGCTRL.reg = 0x12;   // 13-bit
  ADCsync();
  ADC->SAMPCTRL.reg = 0x0;   // sample length default

  int16_t ctrlb = 0x410;     // prescale + resolução/modo
  ADCsync();
  ADC->CTRLB.reg = ctrlb;

  // Descarta primeira conversão após mudar a referência
  S1 = anaRead(A0);
  S2 = anaRead(A1);
  S3 = anaRead(A2);
  S4 = anaRead(A3);
  S5 = anaRead(A4);
  S6 = anaRead(A5);

  pinMode(pinCONTROL_LH, OUTPUT);
  pinMode(pinCONTROL_RH, OUTPUT);
  analogWrite(pinCONTROL_LH, 0);
  analogWrite(pinCONTROL_RH, 0);
}

void loop() {

  // Espera comando de início vindo do PC ('1' inicia a coleta + controle)
  while (serialFlag == '0') {
    analogWrite(pinCONTROL_LH, 0);
    analogWrite(pinCONTROL_RH, 0);
    myEnc.write(0);  // zera o encoder ao aguardar novo início
    if (SerialUSB.available() > 0) {
      serialFlag = SerialUSB.read();
      tempoInicio = millis();
    }
  }

  while (serialFlag != '0') {

    tempoAtual = millis() - tempoInicio;

    /** ----- 1. LEITURA (encoder + células de carga) ----- **/
    encoderPos = myEnc.read();

    S1 = anaRead(A0);
    S2 = anaRead(A1);
    S3 = anaRead(A2);
    S4 = anaRead(A3);
    S5 = anaRead(A4);
    S6 = anaRead(A5);

    /** ----- 2. ENVIA LEITURAS AO PC ----- **/
    // formato CSV: t,encoderPos,S1,S2,S3,S4,S5,S6
    SerialUSB.print(tempoAtual);
    SerialUSB.print(",");
    SerialUSB.print(encoderPos);
    SerialUSB.print(",");
    SerialUSB.print(S1);
    SerialUSB.print(",");
    SerialUSB.print(S2);
    SerialUSB.print(",");
    SerialUSB.print(S3);
    SerialUSB.print(",");
    SerialUSB.print(S4);
    SerialUSB.print(",");
    SerialUSB.print(S5);
    SerialUSB.print(",");
    SerialUSB.println(S6);

    /** ----- 3. RECEBE O OUTPUT DO PID (calculado no PC) ----- **/
    // O PC deve responder com uma linha terminada em '\n':
    //   - um número (ex.: "123.45" ou "-88.0") -> sinal de controle assinado
    //   - "X" -> comando de parada
    int n = SerialUSB.readBytesUntil('\n', lineBuffer, sizeof(lineBuffer) - 1);
    if (n > 0) {
      lineBuffer[n] = '\0';
      if (lineBuffer[0] == 'X') {
        serialFlag = '0';
        controlOutput = 0;
      } else {
        controlOutput = atof(lineBuffer);
      }
    } else {
      // Sem resposta dentro do timeout -> por segurança, zera o sinal de controle
      controlOutput = 0;
    }

    /** ----- 4. ACIONA A PONTE H ----- **/
    double pwm = constrain(fabs(controlOutput), 0, 255);
    if (controlOutput < 0) {
      analogWrite(pinCONTROL_RH, 0);
      analogWrite(pinCONTROL_LH, (int)pwm);
    } else {
      analogWrite(pinCONTROL_LH, 0);
      analogWrite(pinCONTROL_RH, (int)pwm);
    }
  }
}
