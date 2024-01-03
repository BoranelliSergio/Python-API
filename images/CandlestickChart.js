import React, { useEffect, useRef, useState } from 'react';
import { createChart } from 'lightweight-charts';

const Chart = () => {
    const chartContainerRef = useRef();
    const chart = useRef();

    const [remainingTime, setRemainingTime] = useState(0);


  useEffect(() => {
    chart.current = createChart(chartContainerRef.current, { 
      // configurações do gráfico
    });

    const candlestickSeries = chart.current.addCandlestickSeries();
    

    fetch('') 
      .then(res => res.json())
      .then(data => {
        const cdata = data.map(d => {
          return {
            time: d[0] / 1000,
            open: parseFloat(d[1]),
            high: parseFloat(d[2]),
            low: parseFloat(d[3]),
            close: parseFloat(d[4]),
          }
        });

        candlestickSeries.setData(cdata);

        const closeValues = cdata.map(d => d.close);

        
        // Pegando a última vela
        const lastCandle = cdata[cdata.length - 1];

        // Calculando o tempo restante para o fechamento da vela atual
        const currentTime = Date.now() / 1000; // tempo atual em segundos
        const nextCandleTime = lastCandle.time + 15 * 60; // tempo de fechamento previsto da próxima vela em segundos
        setRemainingTime(nextCandleTime - currentTime); // tempo restante em segundos

        // Aguardar o fechamento da vela atual antes de iniciar a próxima contagem regressiva
        const timeoutId = setTimeout(() => {
          setRemainingTime(15 * 60);
        }, remainingTime * 1000);
        
        // Limpar o timeout quando o componente for desmontado
        return () => clearTimeout(timeoutId);
      });

    // Atualizando o tempo restante a cada segundo
    const intervalId = setInterval(() => {
      setRemainingTime(time => time - 1);
    }, 1000);

    // Limpar o intervalo quando o componente for desmontado
    return () => clearInterval(intervalId);
  }, []);

  // Formatando o tempo restante como minutos e segundos
  const minutes = Math.floor(remainingTime / 60);
  const seconds = remainingTime % 60;

  return (
    <div>
       
       <div
        style={{
          width: '1000px',
          height: '50px',
          position: 'absolute',
          top: '45px',
          left: '50%',
          transform: 'translateX(-50%)',
          textAlign: 'center',
          lineHeight: '50px',
          fontWeight: 'bold',
          boxShadow: '0 0 10px rgba(0, 0, 0, 0.5)',
          backgroundColor: '#7B8379'
        }}
      >
        Paridade: TRBUSDT/ Binance Spot - Tempo Grafico: 15M
      </div>
      <div 
        ref={chartContainerRef} 
        style={{ 
          width: '1000px', 
          height: '400px', 
          overflow: 'hidden', 
          position: 'absolute', 
          top: '100px',
          left: '50%', 
          transform: 'translateX(-50%)', 
          boxShadow: '0 0 10px rgba(0, 0, 0, 0.5)' 
        }}
      />
      
      <div 
        style={{ 
        width: '1000px',
          height: '50px',
          position: 'absolute',
          top: '505px',
          left: '50%',
          transform: 'translateX(-50%)',
          textAlign: 'center',
          lineHeight: '50px',
          fontWeight: 'bold',
          boxShadow: '0 0 10px rgba(0, 0, 0, 0.5)',
          backgroundColor: '#510000',
          
        }}
      >
        Tempo restante para o fechamento da vela atual: {minutes}m {seconds}s
      </div>
     
    </div>
  );
};

export default Chart;